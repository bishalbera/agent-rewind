
from __future__ import annotations

import typer
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
app = typer.Typer(add_completion=False, help="Flight recorder & replay for AI agents.")
console = Console()


@app.command()
def seed() -> None:
    """Seed the local SQLite store with the fixed order set."""
    from support_agent import db

    db.seed()
    console.print(f"[green]seeded[/green] {db.db_path()}")


@app.command()
def run(query: str, model: str = typer.Option(None, help="Override the model.")) -> None:
    """Run one support session and print the trace id (look it up in SigNoz)."""
    from rewind.recorder import shutdown
    from support_agent.agent import run_session

    result = run_session(query, model=model)
    console.print(f"\n[bold]answer:[/bold] {result.final_answer}")
    console.print(
        f"\n[dim]session={result.session_id} steps={result.step_count} "
        f"tools={[t['name'] for t in result.tool_calls]}[/dim]"
    )
    console.print(f"[bold cyan]trace_id={result.trace_id}[/bold cyan]")
    shutdown()


@app.command()
def replay(
    trace_id: str,
    model: str = typer.Option(None, help="Override the model for this replay."),
    temperature: float = typer.Option(None, help="Override temperature (models that accept it)."),
    prompt_file: str = typer.Option(None, help="Replace the system prompt (e.g. a hardened one)."),
    source: str = typer.Option("api", help="Trace source: 'api' or 'clickhouse'."),
) -> None:
    """Replay a recorded incident: re-run the session with tools served from
    the recording. Tools are deterministic; the LLM runs fresh."""
    from rewind.recorder import shutdown
    from rewind.replay import replay as do_replay

    res = do_replay(
        trace_id, model=model, temperature=temperature, prompt_file=prompt_file, source=source
    )
    r = res.recorded
    console.print(
        f"[dim]replaying {trace_id}  (run #{res.replay_run})  query: {r.user_query[:60]}[/dim]"
    )
    console.print(f"[dim]recorded: model={r.model} tools={[t.name for t in r.tools]}[/dim]")
    matched = sum(1 for m in res.matches if m.status == "matched")
    console.print(
        f"\ntool calls: {len(res.matches)}  matched={matched}  "
        f"divergences={len(res.divergences)}"
    )
    for m in res.divergences:
        console.print(f"  [yellow]! {m.name} (call #{m.ordinal}): {m.status}[/yellow]")
    console.print(f"\n[bold]replay answer:[/bold] {res.final_answer}")
    console.print(f"\n[bold cyan]replay trace_id={res.trace_id}[/bold cyan] "
                  f"[dim](rewind.replay_of={trace_id})[/dim]")
    shutdown()


@app.command()
def diff(
    original_id: str,
    replay_id: str,
    source: str = typer.Option("api", help="Trace source: 'api' or 'clickhouse'."),
) -> None:
    """Compare an incident trace against a replay, side by side."""
    from rewind.diff import compute_diff, render_diff

    render_diff(compute_diff(original_id, replay_id, source=source), console)


if __name__ == "__main__":
    app()
