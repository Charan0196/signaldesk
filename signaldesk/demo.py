"""The demo: the 5-minute story judges should see.

The narrative is the *learning curve*, not the feature list:

    1. the book of business is loaded (real outcomes: who was saved, who was lost)
    2. the target account gets a verdict and a save play
    3. the same verdict is shown for a memory-less control bank — the generic answer
    4. diffing them is the product

Step 3 is the point. Without precedent the agent still returns something
reasonable — it just has no idea what worked here before.
"""

from __future__ import annotations

import json
import os
import tempfile

from rich.console import Console
from rich.panel import Panel

from .agent import SignalDeskAgent
from .config import Settings
from .memory import MemoryError
from .render import render_finding
from .seed import build_dataset


def _rule(console: Console, title: str) -> None:
    console.rule(f"[bold]{title}[/bold]", style="cyan")


def run_demo(
    console: Console,
    err: Console,
    *,
    reset: bool = False,
    target: str = "Meridian Freight",
) -> int:
    """Seed memory, analyze the target account, and contrast with a blank bank."""
    from .agent import SignalDeskAgent as _A  # noqa: F401 - keeps import local & explicit

    console.print(
        Panel(
            "[bold]SignalDesk[/bold] — churn early-warning with memory\n"
            "Every verdict below is reasoned over a Hindsight memory bank holding "
            "this org's full account history, including which save plays worked "
            "and which customers we lost.",
            border_style="cyan",
        )
    )

    settings = Settings.from_env()
    records = build_dataset("demo")

    with SignalDeskAgent(settings) as ag:
        ag.setup()
        if reset:
            ag.memory.reset_bank()
            console.print("[dim]bank memories cleared[/dim]")

        _rule(console, "1 · Load the book of business")
        console.print(f"[dim]retaining {len(records)} historical signals across 8 accounts…[/dim]")
        ag.ingest_history(records)
        stats = ag.memory.stats()
        console.print(
            f"[green]✓[/green] {stats.get('memories')} memory units  ·  "
            f"{stats.get('accounts')} accounts  ·  "
            f"observations consolidating in the background"
        )

        _rule(console, f"2 · Assess [bold]{target}[/bold] from memory")
        try:
            finding = ag.analyze(target)
        except MemoryError as exc:
            err.print(f"[red]✗ analysis failed: {exc}[/red]")
            return 1
        render_finding(finding, console)

        _rule(console, "3 · The same account, with memory switched OFF")
        control = _control_finding(console, err, target, records)
        if control is None:
            return 0

        _rule(console, "4 · What memory bought")
        _print_diff(console, finding, control)

    console.print()
    console.print(
        "[dim]Try it yourself:  signaldesk assess \"" + target + "\"  |  "
        "signaldesk board  |  signaldesk brief \"" + target + "\"[/dim]"
    )
    return 0


def control_assessment(target: str, records: list[dict] | None = None):
    """Analyze ``target`` against a bank holding ONLY its own signals.

    This is the honest ablation: same model, same prompts, no history — so the
    difference in output is attributable to memory alone. The control bank is
    always torn down afterwards.
    """
    if records is None:
        records = build_dataset("demo")
    own = [r for r in records if r["account"] == target]
    if not own:
        raise MemoryError(f"no signals found for account '{target}' in the dataset")

    base = Settings.from_env()
    control_settings = Settings(
        base_url=base.base_url,
        api_key=base.api_key,
        bank_id=f"signaldesk-control-{os.getpid()}",
        org_name=base.org_name,
        reflect_budget=base.reflect_budget,
    )
    try:
        with SignalDeskAgent(control_settings) as ctl:
            ctl.setup()
            ctl.ingest_history(own)
            return ctl.analyze(target, refresh_brief=False)
    finally:
        _cleanup_bank(control_settings)


def _control_finding(console: Console, err: Console, target: str, records: list[dict]):
    """CLI wrapper around :func:`control_assessment` that reports failures inline."""
    console.print(
        "[dim]building a control bank with only this account's signals, "
        "no prior history, no precedent…[/dim]"
    )
    try:
        return control_assessment(target, records)
    except MemoryError as exc:
        err.print(f"[yellow]control analysis unavailable: {exc}[/yellow]")
        return None


def _cleanup_bank(settings: Settings) -> None:
    """Tear down the throwaway control bank.

    ``delete_bank`` is a coroutine, so it must be awaited — an un-awaited one
    returns immediately and leaks the bank.
    """
    try:
        from hindsight_client import Hindsight

        from .memory import run_sync

        h = Hindsight(base_url=settings.base_url, api_key=settings.api_key, timeout=60.0)
        try:
            run_sync(h.banks.delete_bank(settings.bank_id))
        except Exception:  # noqa: BLE001 - fall back to emptying it
            try:
                h.memory.clear_bank_memories(settings.bank_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _print_diff(console: Console, with_mem, without_mem) -> None:
    a, b = with_mem.assessment, without_mem.assessment
    pa, pb = with_mem.plan, without_mem.plan

    def row(label: str, left, right) -> None:
        console.print(f"  [bold]{label}[/bold]")
        console.print(f"    [green]with memory :[/green] {left}")
        console.print(f"    [dim]without       :[/dim] {right}")

    row("Play recommended", pa.headline_play, pb.headline_play)
    row("Confidence", pa.confidence, pb.confidence)
    row(
        "Cites precedent",
        f"{len(pa.precedent)} comparable account(s) from history",
        "none — no precedent exists in an empty bank",
    )
    row("Risk score", f"{a.risk_score}/100 ({a.risk_tier})", f"{b.risk_score}/100 ({b.risk_tier})")
    row(
        "Plan actions",
        ", ".join(x.action[:44] for x in pa.actions[:3]) or "—",
        ", ".join(x.action[:44] for x in pb.actions[:3]) or "—",
    )
    console.print(
        "\n  [bold cyan]→[/bold cyan] Neither answer is wrong. The memory-backed one is "
        "defensible in a room where the CFO is sitting there."
    )
