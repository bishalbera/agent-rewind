
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rewind import conventions as c
from rewind.recorder import Recorder, Session
from rewind.trace_store import (
    RecordedSession,
    RecordedTool,
    count_replays,
    fetch_spans,
    reconstruct,
)

_SYNTHETIC_MARKER = "__rewind_no_data__"


@dataclass
class ToolMatch:
    """One replayed tool call and how it lined up with the recording."""

    ordinal: int
    name: str
    status: str  # "matched" | "args_mismatch" | "unmatched"


class MockToolRunner:
    """Serves recorded tool responses instead of executing tools.

    Matching is by **(tool_name, per-tool ordinal)**: the k-th call to tool ``T``
    on replay is answered by the k-th recorded response for ``T`` (recorded order
    preserved via ``step_index``). This is deliberately robust to the replayed
    agent grouping its tool calls into a different number of turns — global step
    indices would misalign there and the agent would dodge, rather than face, the
    recorded environment (e.g. a poisoned document). Decisions:

      * k-th call of T with the same args as the k-th recorded T -> serve it (matched).
      * same slot, different args -> args-mismatch divergence; still serve the
        recorded response (keep the run on the recorded rails).
      * no k-th recorded call of T -> behavioural divergence: serve a
        clearly-marked synthetic "no data" response and flag it.
    """

    def __init__(self, recorded: RecordedSession) -> None:
        self._by_tool: dict[str, list[RecordedTool]] = defaultdict(list)
        for tool in sorted(recorded.tools, key=lambda t: t.step_index):
            self._by_tool[tool.name].append(tool)
        self._seen: dict[str, int] = defaultdict(int)
        self.matches: list[ToolMatch] = []

    @property
    def divergences(self) -> list[ToolMatch]:
        return [m for m in self.matches if m.status != "matched"]

    def run(
        self, session: Session, name: str, args: dict[str, Any], call_id: str
    ) -> tuple[Any, bool]:
        k = self._seen[name]
        self._seen[name] += 1
        recorded = self._by_tool.get(name, [])
        rec = recorded[k] if k < len(recorded) else None
        with session.tool_call(name=name, args=args, call_id=call_id) as span:
            if rec is None:
                # Behavioural divergence — the replayed agent asked for something
                # the recording never produced. Serve a marked synthetic response.
                span.divergence = c.DIVERGENCE_UNMATCHED
                span.response = {
                    _SYNTHETIC_MARKER: True,
                    "note": "no recorded data for this call — divergent path",
                    "tool": name,
                    "ordinal": k,
                }
                self.matches.append(ToolMatch(k, name, "unmatched"))
                return span.response, False

            if rec.args != args:
                span.divergence = c.DIVERGENCE_ARGS
                span.response = rec.response
                self.matches.append(ToolMatch(k, name, "args_mismatch"))
                return rec.response, bool(rec.error)

            span.response = rec.response
            self.matches.append(ToolMatch(k, name, "matched"))
            return rec.response, bool(rec.error)


@dataclass
class ReplayResult:
    replay_of: str
    replay_run: int
    trace_id: str
    session_id: str
    final_answer: str
    model: str
    recorded: RecordedSession
    matches: list[ToolMatch] = field(default_factory=list)

    @property
    def divergences(self) -> list[ToolMatch]:
        return [m for m in self.matches if m.status != "matched"]


def replay(
    trace_id: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    prompt_file: str | None = None,
    source: str = "api",
) -> ReplayResult:
    """Fetch a recorded trace and re-run its session with mocked tools.

    Emits a brand-new trace tagged ``rewind.replay_of=<trace_id>`` and
    ``rewind.replay_run=<n>``. Returns a :class:`ReplayResult` for the diff.
    """
    from support_agent.agent import run_session  # local import: avoids a cycle

    spans = fetch_spans(trace_id, source=source)
    recorded = reconstruct(spans, trace_id)

    run_n = count_replays(trace_id) + 1
    system = recorded.system
    if prompt_file:
        with open(prompt_file) as fh:
            system = fh.read()

    runner = MockToolRunner(recorded)
    recorder = Recorder(replay_of=trace_id, replay_run=run_n)

    result = run_session(
        recorded.user_query,
        recorder=recorder,
        tool_runner=runner,
        model=model or recorded.model,
        temperature=temperature if temperature is not None else recorded.temperature,
        system=system,
        max_tokens=recorded.max_tokens or 1024,
    )

    return ReplayResult(
        replay_of=trace_id,
        replay_run=run_n,
        trace_id=result.trace_id,
        session_id=result.session_id,
        final_answer=result.final_answer,
        model=model or recorded.model,
        recorded=recorded,
        matches=runner.matches,
    )
