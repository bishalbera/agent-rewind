
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

from rewind import conventions as c


@dataclass
class Span:
    """A single recorded span, normalised across backends."""

    name: str
    attributes: dict[str, str]
    duration_nano: int = 0
    has_error: bool = False

    def attr(self, key: str) -> str | None:
        return self.attributes.get(key)


@dataclass
class RecordedTool:
    """One recorded tool interaction, keyed by its step index."""

    step_index: int
    name: str
    args: dict[str, Any]
    response: Any
    error: str | None = None


@dataclass
class RecordedSession:
    """Everything needed to replay a session: the deterministic environment."""

    trace_id: str
    session_id: str
    user_query: str
    system: str
    model: str
    temperature: float | None
    max_tokens: int | None
    tools: list[RecordedTool] = field(default_factory=list)

    def tools_by_step(self) -> dict[int, RecordedTool]:
        return {t.step_index: t for t in self.tools}


# --------------------------------------------------------------------------
# Reconstruction (pure — unit tested)
# --------------------------------------------------------------------------

def reconstruct(spans: list[Span], trace_id: str) -> RecordedSession:
    """Rebuild a :class:`RecordedSession` from a trace's spans.

    Uses ``rewind.step_kind`` to classify spans and ``rewind.step_index`` to
    order tool interactions — span timestamps are not reliable enough because
    tool calls within one turn can interleave.
    """
    root: Span | None = None
    llm_spans: list[Span] = []
    tool_spans: list[Span] = []
    for s in spans:
        kind = s.attr(c.REWIND_STEP_KIND)
        if kind == c.STEP_KIND_LLM:
            llm_spans.append(s)
        elif kind == c.STEP_KIND_TOOL:
            tool_spans.append(s)
        elif s.name == "agent.session" or s.attr(c.REWIND_SESSION_ID):
            root = root or s

    if root is None and not llm_spans:
        raise ValueError(f"trace {trace_id} has no recognizable Rewind session spans")

    llm_spans.sort(key=lambda s: _int(s.attr(c.REWIND_STEP_INDEX)))
    first_llm = llm_spans[0] if llm_spans else None

    user_query = (root.attr("rewind.user_query") if root else None) or ""
    session_id = (
        (root.attr(c.REWIND_SESSION_ID) if root else None)
        or (first_llm.attr(c.REWIND_SESSION_ID) if first_llm else None)
        or ""
    )
    system = first_llm.attr(c.GEN_AI_SYSTEM_INSTRUCTIONS) if first_llm else ""
    model = (first_llm.attr(c.GEN_AI_REQUEST_MODEL) if first_llm else None) or ""
    temperature = _float(first_llm.attr(c.GEN_AI_REQUEST_TEMPERATURE)) if first_llm else None
    max_tokens = _int_or_none(first_llm.attr(c.GEN_AI_REQUEST_MAX_TOKENS)) if first_llm else None

    tools = [
        RecordedTool(
            step_index=_int(s.attr(c.REWIND_STEP_INDEX)),
            name=s.attr(c.GEN_AI_TOOL_NAME) or "",
            args=_load(s.attr(c.REWIND_TOOL_ARGS)),
            response=_load(s.attr(c.REWIND_TOOL_RESPONSE)),
            error=s.attr(c.REWIND_TOOL_ERROR),
        )
        for s in tool_spans
    ]
    tools.sort(key=lambda t: t.step_index)

    return RecordedSession(
        trace_id=trace_id,
        session_id=session_id,
        user_query=user_query,
        system=system or "",
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
    )


@dataclass
class TraceSummary:
    """Aggregate view of one trace, used to diff an incident against its replay."""

    trace_id: str
    replay_of: str | None
    replay_run: int | None
    chaos: str | None
    step_count: int
    tool_sequence: list[str]
    tool_calls: list[tuple[str, dict[str, Any]]]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    divergences: int
    error_tools: list[str]
    final_answer: str


def summarize_trace(spans: list[Span], trace_id: str) -> TraceSummary:
    """Reduce a trace's spans to the fields the diff compares. Pure/testable."""
    root = next((s for s in spans if s.name == "agent.session"), None)
    llm = sorted(
        (s for s in spans if s.attr(c.REWIND_STEP_KIND) == c.STEP_KIND_LLM),
        key=lambda s: _int(s.attr(c.REWIND_STEP_INDEX)),
    )
    tools = sorted(
        (s for s in spans if s.attr(c.REWIND_STEP_KIND) == c.STEP_KIND_TOOL),
        key=lambda s: _int(s.attr(c.REWIND_STEP_INDEX)),
    )

    input_tokens = sum(_int(s.attr(c.GEN_AI_USAGE_INPUT_TOKENS)) or 0 for s in llm)
    output_tokens = sum(_int(s.attr(c.GEN_AI_USAGE_OUTPUT_TOKENS)) or 0 for s in llm)
    cost = sum(_float(s.attr("rewind.cost_usd")) or 0.0 for s in llm)
    latency_ms = (root.duration_nano / 1e6) if root else 0.0

    final_answer = ""
    if llm:
        blocks = _load(llm[-1].attr(c.GEN_AI_OUTPUT_MESSAGES)) or []
        if isinstance(blocks, list):
            final_answer = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )

    return TraceSummary(
        trace_id=trace_id,
        replay_of=(root.attr(c.REWIND_REPLAY_OF) if root else None),
        replay_run=_int_or_none(root.attr(c.REWIND_REPLAY_RUN)) if root else None,
        chaos=(root.attr(c.REWIND_CHAOS_SCENARIO) if root else None) or None,
        step_count=len(llm) + len(tools),
        tool_sequence=[s.attr(c.GEN_AI_TOOL_NAME) or "" for s in tools],
        tool_calls=[
            (s.attr(c.GEN_AI_TOOL_NAME) or "", _load(s.attr(c.REWIND_TOOL_ARGS)) or {})
            for s in tools
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
        divergences=sum(1 for s in tools if s.attr(c.REWIND_DIVERGENCE) in ("true", "True")),
        error_tools=[
            s.attr(c.GEN_AI_TOOL_NAME) or "" for s in tools if s.attr(c.REWIND_TOOL_ERROR)
        ],
        final_answer=final_answer,
    )


def _int(v: str | None) -> int:
    try:
        return int(float(v)) if v is not None else -1
    except (TypeError, ValueError):
        return -1


def _int_or_none(v: str | None) -> int | None:
    n = _int(v)
    return n if n >= 0 else None


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _load(v: str | None) -> Any:
    if v is None:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def fetch_spans(trace_id: str, source: str = "api") -> list[Span]:
    """Fetch a trace's spans from SigNoz. ``source`` is ``api`` or ``clickhouse``."""
    if source == "clickhouse":
        return _fetch_clickhouse(trace_id)
    return _fetch_api(trace_id)


def _fetch_api(trace_id: str) -> list[Span]:
    base = os.getenv("SIGNOZ_API_URL", "http://localhost:8080").rstrip("/")
    key = os.getenv("SIGNOZ_API_KEY", "")
    resp = httpx.get(
        f"{base}/api/v1/traces/{trace_id}",
        headers={"SIGNOZ-API-KEY": key},
        timeout=15.0,
    )
    resp.raise_for_status()
    return parse_api_payload(resp.json())


def parse_api_payload(data: Any) -> list[Span]:
    """Parse the ``GET /api/v1/traces/{id}`` response into :class:`Span` objects.

    Split out from the HTTP call so it can be unit-tested against a fixture.
    Each event row carries parallel ``TagsKeys``/``TagsValues`` arrays.
    """
    if not data:
        return []
    item = data[0]
    cols = {name: i for i, name in enumerate(item["columns"])}
    spans: list[Span] = []
    for row in item.get("events", []):
        keys = row[cols["TagsKeys"]] or []
        vals = row[cols["TagsValues"]] or []
        spans.append(
            Span(
                name=row[cols["Name"]],
                attributes=dict(zip(keys, vals, strict=False)),
                duration_nano=_int(str(row[cols["DurationNano"]])),
                has_error=bool(row[cols["HasError"]]),
            )
        )
    return spans


def _fetch_clickhouse(trace_id: str) -> list[Span]:
    container = os.getenv("SIGNOZ_CLICKHOUSE_CONTAINER", "signoz-telemetrystore-clickhouse-0-0")
    query = (
        "SELECT name, attributes_string, attributes_number, duration_nano, "
        "has_error FROM signoz_traces.distributed_signoz_index_v3 "
        f"WHERE trace_id = '{trace_id}' FORMAT JSONEachRow"
    )
    out = subprocess.run(
        ["docker", "exec", container, "clickhouse-client", "-q", query],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    spans: list[Span] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        attrs = {k: str(v) for k, v in {**row.get("attributes_string", {}),
                                        **row.get("attributes_number", {})}.items()}
        spans.append(
            Span(
                name=row["name"],
                attributes=attrs,
                duration_nano=int(row.get("duration_nano", 0)),
                has_error=bool(row.get("has_error", False)),
            )
        )
    return spans


def count_replays(trace_id: str) -> int:
    """How many replays of this trace already exist (best-effort; 0 on failure)."""
    container = os.getenv("SIGNOZ_CLICKHOUSE_CONTAINER", "signoz-telemetrystore-clickhouse-0-0")
    query = (
        "SELECT count() FROM signoz_traces.distributed_signoz_index_v3 "
        f"WHERE name = 'agent.session' AND attributes_string['{c.REWIND_REPLAY_OF}'] = '{trace_id}'"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", container, "clickhouse-client", "-q", query],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return int(out or "0")
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0
