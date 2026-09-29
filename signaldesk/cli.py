"""SignalDesk CLI.

    signaldesk setup                      create/configure the memory bank
    signaldesk ingest --account X -f f    store a raw signal (file, -f -, or --text)
    signaldesk assess X                   risk verdict + save play
    signaldesk brief X                    the self-refreshing account brief
    signaldesk board [X ...]              portfolio triage, highest risk first
    signaldesk accounts                   what is in memory
    signaldesk seed [--dataset demo]      load the historical book of business
    signaldesk demo [--reset]             the full learning-curve story
    signaldesk stats                      memory counters
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from .agent import SignalDeskAgent
from .config import Settings
from .memory import MemoryError
from .render import render_brief, render_finding, render_markdown, render_triage

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="SignalDesk — churn early-warning and save-play memory, on Hindsight.",
)
console = Console()
err = Console(stderr=True)

CHANNELS = ["call", "email", "ticket", "qbr", "slack", "note"]


def _die(msg: str, code: int = 1) -> None:
    err.print(f"[red]✗ {msg}[/red]")
    raise typer.Exit(code)


def _agent() -> SignalDeskAgent:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        return SignalDeskAgent(Settings.from_env())
    except SystemExit as exc:
        _die(str(exc))


def _read_text(file: str | None, text: str | None) -> str:
    if text:
        return text
    if file in (None, "-"):
        if sys.stdin.isatty():
            _die("no input: pass --text, --file, or pipe via stdin")
        return sys.stdin.read()
    p = Path(file).expanduser()
    if not p.exists():
        _die(f"file not found: {p}")
    return p.read_text(encoding="utf-8")


@app.command()
def setup() -> None:
    """Create or reconfigure the memory bank, its mission, and its guardrails."""
    with _agent() as ag:
        res = ag.setup()
    verb = "created" if res.get("created") else "updated"
    console.print(f"[green]✓[/green] memory bank [bold]{res['bank']}[/bold] {verb}")
    console.print("[dim]  mission, observation policy and guardrail directives installed[/dim]")


@app.command()
def ingest(
    account: str = typer.Option(..., "--account", "-a", help="Customer account name"),
    file: str = typer.Option(None, "--file", "-f", help="Signal file, or - for stdin"),
    text: str = typer.Option(None, "--text", "-t", help="Inline signal text"),
    channel: str = typer.Option("note", "--channel", "-c", help=f"One of: {', '.join(CHANNELS)}"),
    when: str = typer.Option(None, "--date", "-d", help="When the signal happened (YYYY-MM-DD)"),
) -> None:
    """Store a raw customer signal into memory."""
    body = _read_text(file, text)
    ts = None
    if when:
        try:
            ts = datetime.strptime(when, "%Y-%m-%d")
        except ValueError:
            _die("--date must be YYYY-MM-DD")
    with _agent() as ag:
        ag.setup()
        res = ag.ingest(body, account, channel=channel, when=ts)
    console.print(f"[green]✓[/green] retained for [bold]{account}[/bold] via {channel} "
                  f"({res.get('items_count', 1)} unit(s))")


@app.command()
def assess(
    account: str = typer.Argument(..., help="Customer account to assess"),
    md: bool = typer.Option(False, "--md", help="Emit markdown instead of a rendered panel"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON"),
    no_brief: bool = typer.Option(False, "--no-brief", help="Skip the mental-model refresh"),
) -> None:
    """Diagnose one account and prescribe a save play."""
    with _agent() as ag:
        try:
            finding = ag.analyze(account, refresh_brief=not no_brief)
        except MemoryError as exc:
            _die(str(exc))
    if json_out:
        console.print_json(json.dumps(finding.to_dict(), default=str))
    elif md:
        console.print(render_markdown(finding))
    else:
        render_brief(finding.brief, account, console)
        render_finding(finding, console)


@app.command()
def brief(account: str = typer.Argument(...)) -> None:
    """Print the self-refreshing account brief (Hindsight mental model)."""
    with _agent() as ag:
        text = ag.memory.get_brief(account)
        if not text:
            ag.memory.refresh_brief(account)
            text = ag.memory.get_brief(account)
    render_brief(text, account, console)


@app.command()
def board(
    accounts: list[str] = typer.Argument(None, help="Accounts to triage (default: all in memory)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max accounts to analyze"),
) -> None:
    """Triage accounts, highest risk first."""
    with _agent() as ag:
        targets = accounts or ag.memory.known_accounts()
        if not targets:
            _die("no accounts in memory yet — run `signaldesk seed` or `signaldesk ingest`")
        targets = targets[:limit]
        console.print(f"[dim]analyzing {len(targets)} account(s) against memory…[/dim]")
        findings = ag.triage(targets)
    render_triage(findings, console)


@app.command()
def accounts() -> None:
    """List the customer accounts held in memory."""
    with _agent() as ag:
        names = ag.memory.known_accounts()
    if not names:
        console.print("[yellow]memory is empty — run `signaldesk seed`[/yellow]")
        return
    for n in names:
        console.print(f"  · {n}")


@app.command()
def stats() -> None:
    """Show memory counters."""
    with _agent() as ag:
        s = ag.memory.stats()
    console.print_json(json.dumps(s, default=str))


@app.command()
def seed(
    dataset: str = typer.Option("demo", "--dataset", help="demo | blank"),
    reset: bool = typer.Option(False, "--reset", help="Wipe memories first"),
) -> None:
    """Load a realistic historical book of business into memory."""
    from .seed import build_dataset, DATASETS

    if dataset not in DATASETS:
        _die(f"unknown dataset '{dataset}' (have: {', '.join(DATASETS)})")
    with _agent() as ag:
        ag.setup()
        if reset:
            ag.memory.reset_bank()
            console.print("[dim]bank memories cleared[/dim]")
        records = build_dataset(dataset)
        console.print(f"[dim]retaining {len(records)} historical signals…[/dim]")
        res = ag.ingest_history(records)
    console.print(f"[green]✓[/green] retained {res.get('items_count', len(records))} signals "
                  f"across {len({r['account'] for r in records})} accounts")


@app.command()
def demo(
    reset: bool = typer.Option(False, "--reset", help="Wipe memories and rebuild from scratch"),
    target: str = typer.Option("Meridian Freight", "--target", help="Account to demo on"),
) -> None:
    """Run the end-to-end story: seed the book, then assess the target account."""
    from .demo import run_demo

    run_demo(console, err, reset=reset, target=target)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8420, "--port", "-p", help="Port"),
) -> None:
    """Launch the web dashboard (the same agent, in a browser)."""
    try:
        import uvicorn
    except ImportError:
        _die("uvicorn is not installed — run: uv pip install 'uvicorn[standard]' fastapi")
    from .web import STATIC_DIR  # noqa: F401 - validates the asset dir exists

    console.print(f"[green]▶[/green] SignalDesk dashboard on [bold]http://{host}:{port}[/bold]")
    uvicorn.run("signaldesk.web:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
