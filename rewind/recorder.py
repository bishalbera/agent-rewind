
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from rewind import conventions as c
from rewind.pricing import cost_usd

log = logging.getLogger(c.INSTRUMENTATION_NAME)

_CONFIGURED = False


def configure(service_name: str | None = None) -> None:
    """Wire up OTLP/HTTP export to SigNoz for traces, metrics, and logs.

    Idempotent — safe to call from every entry point. Reads endpoint/protocol
    from the standard ``OTEL_EXPORTER_OTLP_*`` env vars (see .env.example).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "support-agent")
    resource = Resource.create({"service.name": service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    _CONFIGURED = True


def shutdown() -> None:
    """Flush and stop the exporters. Call at process exit so batches export."""
    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        if hasattr(provider, "shutdown"):
            provider.shutdown()


def _json(value: Any) -> str:
    """Compact, deterministic JSON for span attributes (sorted keys for caching-friendly diffs)."""
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


@dataclass
class _Instruments:
    """Metric instruments, created once per Recorder."""

    cost: Any
    llm_duration: Any
    tool_duration: Any
    tool_errors: Any


@dataclass
class LLMResult:
    """What an LLM span learns after the call returns; set by the caller."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    response_id: str | None = None
    output_messages: list[dict[str, Any]] = field(default_factory=list)


class Recorder:
    """Creates the tracer/meter and hands out session, LLM, and tool spans."""

    def __init__(self, *, replay_of: str | None = None, replay_run: int | None = None) -> None:
        configure()
        self.tracer = trace.get_tracer(c.INSTRUMENTATION_NAME)
        meter = metrics.get_meter(c.INSTRUMENTATION_NAME)
        self._inst = _Instruments(
            cost=meter.create_counter(
                c.METRIC_COST_USD, unit="USD", description="LLM cost per call, from pricing table"
            ),
            llm_duration=meter.create_histogram(
                c.METRIC_LLM_DURATION, unit="ms", description="LLM call wall-clock latency"
            ),
            tool_duration=meter.create_histogram(
                c.METRIC_TOOL_DURATION, unit="ms", description="Tool call wall-clock latency"
            ),
            tool_errors=meter.create_counter(
                c.METRIC_TOOL_ERRORS, unit="1", description="Tool calls that raised or errored"
            ),
        )
        self._replay_of = replay_of
        self._replay_run = replay_run

    @contextmanager
    def session(
        self, session_id: str, *, user_query: str, chaos: str | None = None
    ) -> Iterator[Session]:
        """Root span for one agent run. Yields a :class:`Session` handle.

        On a replay run, tags the root span with ``rewind.replay_of`` /
        ``rewind.replay_run`` so the diff dashboard can pair the two traces.
        """
        with self.tracer.start_as_current_span("agent.session") as span:
            span.set_attribute(c.REWIND_SESSION_ID, session_id)
            span.set_attribute("rewind.user_query", user_query)
            if chaos:
                span.set_attribute(c.REWIND_CHAOS_SCENARIO, chaos)
            if self._replay_of:
                span.set_attribute(c.REWIND_REPLAY_OF, self._replay_of)
            if self._replay_run is not None:
                span.set_attribute(c.REWIND_REPLAY_RUN, self._replay_run)
            trace_id = f"{span.get_span_context().trace_id:032x}"
            log.info(
                "session start",
                extra={"rewind.session_id": session_id, "rewind.trace_id": trace_id},
            )
            yield Session(self, span, session_id, trace_id)
            log.info("session end", extra={"rewind.session_id": session_id})

    # --- metric helpers, called from the span context managers ---

    def _record_cost(self, model: str, in_tok: int, out_tok: int) -> float:
        usd = cost_usd(model, in_tok, out_tok)
        self._inst.cost.add(usd, {c.GEN_AI_REQUEST_MODEL: model})
        return usd


class Session:
    """Per-run handle: assigns step indices and opens LLM/tool child spans."""

    def __init__(
        self, recorder: Recorder, span: trace.Span, session_id: str, trace_id: str
    ) -> None:
        self._rec = recorder
        self._root = span
        self.session_id = session_id
        self.trace_id = trace_id
        self._step = 0

    def _next_step(self) -> int:
        s = self._step
        self._step += 1
        return s

    @contextmanager
    def llm_call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        provider: str = c.PROVIDER_ANTHROPIC,
    ) -> Iterator[LLMResult]:
        """Child span for one LLM invocation.

        Yields an :class:`LLMResult` the caller fills in after the API returns;
        the span reads it back on exit to record usage, cost, and the completion.
        """
        step = self._next_step()
        result = LLMResult(model=model)
        start = time.monotonic()
        with self._rec.tracer.start_as_current_span(f"chat {model}") as span:
            span.set_attribute(c.REWIND_SESSION_ID, self.session_id)
            span.set_attribute(c.REWIND_STEP_INDEX, step)
            span.set_attribute(c.REWIND_STEP_KIND, c.STEP_KIND_LLM)
            span.set_attribute(c.GEN_AI_OPERATION_NAME, c.OP_CHAT)
            span.set_attribute(c.GEN_AI_REQUEST_MODEL, model)
            for k, v in c.provider_attributes(provider).items():
                span.set_attribute(k, v)
            if temperature is not None:
                span.set_attribute(c.GEN_AI_REQUEST_TEMPERATURE, temperature)
            if max_tokens is not None:
                span.set_attribute(c.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)
            span.set_attribute(c.GEN_AI_SYSTEM_INSTRUCTIONS, system)
            span.set_attribute(c.GEN_AI_INPUT_MESSAGES, _json(messages))
            try:
                yield result
            finally:
                dur_ms = (time.monotonic() - start) * 1000
                span.set_attribute(c.GEN_AI_RESPONSE_MODEL, result.model)
                if result.response_id:
                    span.set_attribute(c.GEN_AI_RESPONSE_ID, result.response_id)
                if result.finish_reason:
                    span.set_attribute(c.GEN_AI_RESPONSE_FINISH_REASONS, [result.finish_reason])
                span.set_attribute(c.GEN_AI_USAGE_INPUT_TOKENS, result.input_tokens)
                span.set_attribute(c.GEN_AI_USAGE_OUTPUT_TOKENS, result.output_tokens)
                span.set_attribute(c.GEN_AI_OUTPUT_MESSAGES, _json(result.output_messages))
                usd = self._rec._record_cost(
                    result.model, result.input_tokens, result.output_tokens
                )
                span.set_attribute("rewind.cost_usd", usd)
                self._rec._inst.llm_duration.record(dur_ms, {c.GEN_AI_REQUEST_MODEL: result.model})

    @contextmanager
    def tool_call(
        self, *, name: str, args: dict[str, Any], call_id: str | None = None
    ) -> Iterator[ToolSpan]:
        """Child span for one tool invocation. Yields a handle to set the response."""
        step = self._next_step()
        handle = ToolSpan()
        start = time.monotonic()
        with self._rec.tracer.start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute(c.REWIND_SESSION_ID, self.session_id)
            span.set_attribute(c.REWIND_STEP_INDEX, step)
            span.set_attribute(c.REWIND_STEP_KIND, c.STEP_KIND_TOOL)
            span.set_attribute(c.GEN_AI_OPERATION_NAME, c.OP_EXECUTE_TOOL)
            span.set_attribute(c.GEN_AI_TOOL_NAME, name)
            if call_id:
                span.set_attribute(c.GEN_AI_TOOL_CALL_ID, call_id)
            span.set_attribute(c.REWIND_TOOL_ARGS, _json(args))
            try:
                yield handle
            finally:
                dur_ms = (time.monotonic() - start) * 1000
                if handle.error is not None:
                    span.set_attribute(c.REWIND_TOOL_ERROR, handle.error)
                    span.set_status(Status(StatusCode.ERROR, handle.error))
                    self._rec._inst.tool_errors.add(1, {c.GEN_AI_TOOL_NAME: name})
                span.set_attribute(c.REWIND_TOOL_RESPONSE, _json(handle.response))
                self._rec._inst.tool_duration.record(dur_ms, {c.GEN_AI_TOOL_NAME: name})


@dataclass
class ToolSpan:
    """Mutable handle a tool span fills in with its result."""

    response: Any = None
    error: str | None = None
