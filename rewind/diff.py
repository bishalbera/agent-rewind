
from __future__ import annotations

import difflib
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from rewind.trace_store import TraceSummary, fetch_spans, summarize_trace


@dataclass
class DiffResult:
    original: TraceSummary
    replay: TraceSummary
    answer_similarity: float


def compute_diff(original_id: str, replay_id: str, source: str = "api") -> DiffResult:
    a = summarize_trace(fetch_spans(original_id, source=source), original_id)
    b = summarize_trace(fetch_spans(replay_id, source=source), replay_id)
    sim = difflib.SequenceMatcher(None, a.final_answer, b.final_answer).ratio()
    return DiffResult(a, b, sim)


def _tools_repr(summary: TraceSummary) -> str:
    return "\n".join(f"{n}({_args(a)})" for n, a in summary.tool_calls) or "—"


def _args(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())


def render_diff(result: DiffResult, console: Console | None = None) -> None:
    console = console or Console()
    a, b = result.original, result.replay

    table = Table(title="Rewind diff — incident vs replay", show_lines=True)
    table.add_column("", style="bold", no_wrap=True)
    table.add_column("original (incident)", overflow="fold")
    table.add_column("replay", overflow="fold")

    def row(label: str, va: object, vb: object, flag: bool = False) -> None:
        style = "yellow" if flag else None
        sa, sb = str(va), str(vb)
        table.add_row(label, f"[{style}]{sa}[/{style}]" if style else sa,
                      f"[{style}]{sb}[/{style}]" if style else sb)

    row("trace_id", a.trace_id, b.trace_id)
    row("chaos", a.chaos or "—", b.chaos or "—")
    row("replay_of", a.replay_of or "—", b.replay_of or "—")
    row("steps", a.step_count, b.step_count, a.step_count != b.step_count)
    row("tool calls", _tools_repr(a), _tools_repr(b), a.tool_calls != b.tool_calls)
    row("divergences", a.divergences, b.divergences, b.divergences > 0)
    row("tool errors", ", ".join(a.error_tools) or "—", ", ".join(b.error_tools) or "—",
        bool(a.error_tools) != bool(b.error_tools))
    row("input tokens", a.input_tokens, b.input_tokens)
    row("output tokens", a.output_tokens, b.output_tokens)
    row("cost (USD)", f"{a.cost_usd:.5f}", f"{b.cost_usd:.5f}")
    row("latency (ms)", f"{a.latency_ms:.0f}", f"{b.latency_ms:.0f}",
        abs(a.latency_ms - b.latency_ms) > max(a.latency_ms, b.latency_ms) * 0.5)
    row("final answer", a.final_answer[:280] or "—", b.final_answer[:280] or "—")

    console.print(table)
    pct = result.answer_similarity * 100
    verdict = "answers largely agree" if pct > 70 else "answers diverged — behaviour changed"
    console.print(f"final-answer similarity: [bold]{pct:.0f}%[/bold]  ({verdict})")
