
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


if __name__ == "__main__":
    app()
