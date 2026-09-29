"""Terminal rendering. Citations are shown on purpose — a risk verdict the rep
cannot audit is worthless, and judges should see the memory underneath it."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agent import Finding
from .schemas import RiskAssessment, SavePlan

TIER_STYLE = {
    "critical": "bold white on red",
    "high": "bold red",
    "medium": "yellow",
    "low": "green",
}
TIER_BAR = {"critical": "█" * 8, "high": "████" + "░" * 4, "medium": "██" + "░" * 6, "low": "█" + "░" * 7}


def render_finding(finding: Finding, console: Console) -> None:
    a, p = finding.assessment, finding.plan
    console.print()
    console.print(
        Panel(
            f"[bold]{a.account}[/bold]  {TIER_BAR.get(a.risk_tier, '')}  "
            f"[{TIER_STYLE.get(a.risk_tier, '')}]{a.risk_tier.upper()}[/] "
            f"{a.risk_score}/100  ·  confidence: {a.confidence}",
            title="⚠  CHURN RISK",
            border_style="red" if a.risk_tier in ("critical", "high") else "yellow",
        )
    )

    console.print(f"[bold]Primary driver[/bold]\n  {a.primary_driver}")
    if a.drivers:
        console.print("\n[bold]Also contributing[/bold]")
        for d in a.drivers:
            console.print(f"  · {d}")

    last_contact = f"{a.silent_days} days ago" if a.silent_days is not None else "unknown"
    console.print(
        f"[dim]Champion[/dim] {a.champion_status or 'unknown'}\n"
        f"[dim]Last contact[/dim] {last_contact}\n"
        f"[dim]Would change this[/dim] {a.what_would_change_this or '—'}"
    )

    if a.evidence:
        console.print("\n[bold cyan]Evidence from memory[/bold cyan]")
        for ev in a.evidence[:6]:
            when = ev.date or "undated"
            console.print(f"  [dim]{when}[/dim] [cyan]·[/cyan] [dim]{ev.source}[/dim]")
            console.print(f"    [italic]{ev.quote}[/italic]")

    console.print(
        Panel(
            f"[bold green]{p.headline_play}[/bold green]\n"
            f"[dim]confidence:[/dim] {p.confidence}\n\n{p.why_this_play}",
            title="▶  SAVE PLAY",
            border_style="green",
        )
    )

    if p.actions:
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("#", style="dim", width=3)
        t.add_column("Action")
        t.add_column("Owner", style="cyan")
        t.add_column("By", style="magenta")
        t.add_column("Success signal", style="dim")
        for i, act in enumerate(p.actions, 1):
            t.add_row(
                str(i),
                act.action + (f"\n[dim]{act.rationale}[/dim]" if act.rationale else ""),
                act.owner or "[dim]unassigned[/dim]",
                f"d+{act.within_days}" if act.within_days is not None else "[dim]—[/dim]",
                act.success_signal or "[dim]not specified[/dim]",
            )
        console.print(t)

    if p.precedent:
        console.print("\n[bold]Precedent — accounts this play worked on[/bold]")
        for pre in p.precedent:
            console.print(f"  · {pre}")
    if p.counter_evidence:
        console.print(f"\n[bold yellow]Watch out:[/bold yellow] {p.counter_evidence}")
    if p.expected_effect:
        console.print(f"[bold]Expected effect:[/bold] {p.expected_effect}")
    if a.reasoning:
        console.print(f"\n[dim]{a.reasoning}[/dim]")


def render_triage(findings: list[Finding], console: Console) -> None:
    """The 5-second view: which accounts are bleeding, and what to do first."""
    t = Table(title="SignalDesk — Portfolio Risk Board", title_justify="left", header_style="bold")
    t.add_column("Account", style="bold")
    t.add_column("Risk", width=10)
    t.add_column("Score", justify="right", width=6)
    t.add_column("Primary driver")
    t.add_column("Play", style="green")

    for f in findings:
        a, p = f.assessment, f.plan
        failed = a.primary_driver.startswith("analysis failed")
        t.add_row(
            a.account,
            f"[{TIER_STYLE.get(a.risk_tier, '')}]{a.risk_tier.upper()}[/]" if not failed else "[dim]n/a[/dim]",
            str(a.risk_score) if not failed else "—",
            (a.primary_driver or "")[:78],
            (p.headline_play or "")[:40],
        )
    console.print()
    console.print(t)


def render_markdown(finding: Finding) -> str:
    """Machine-readable export — pipes cleanly into a CRM note or a PR body."""
    a, p = finding.assessment, finding.plan
    lines = [
        f"## {a.account} — {a.risk_tier.upper()} risk ({a.risk_score}/100)",
        "",
        f"**Primary driver:** {a.primary_driver}",
    ]
    if a.drivers:
        lines += ["", "**Also contributing:**"] + [f"- {d}" for d in a.drivers]
    lines += [
        "",
        f"**Champion:** {a.champion_status}  ",
        f"**Last contact:** {a.silent_days} days ago" if a.silent_days is not None else "**Last contact:** unknown  ",
        f"**Would change this:** {a.what_would_change_this}",
    ]
    if a.evidence:
        lines += ["", "**Evidence from memory**"]
        for ev in a.evidence:
            lines.append(f"- `{ev.date}` ({ev.source}) — {ev.quote}")
    lines += [
        "",
        f"### Save play: {p.headline_play}",
        "",
        p.why_this_play,
        "",
        "| # | Action | Owner | By | Success signal |",
        "|---|--------|-------|----|----------------|",
    ]
    for i, act in enumerate(p.actions, 1):
        lines.append(
            f"| {i} | {act.action} | {act.owner or '_unassigned_'} | "
            f"{'d+' + str(act.within_days) if act.within_days is not None else '—'} | "
            f"{act.success_signal or '_not specified_'} |"
        )
    if p.precedent:
        lines += ["", "**Precedent:**"] + [f"- {x}" for x in p.precedent]
    if p.counter_evidence:
        lines += ["", f"**Watch out:** {p.counter_evidence}"]
    lines += ["", f"**Expected effect:** {p.expected_effect}", "", f"**Confidence:** {p.confidence}"]
    return "\n".join(lines)


def render_brief(brief: str | None, account: str, console: Console) -> None:
    if not brief:
        console.print(f"[yellow]No stored brief for {account} yet.[/yellow]")
        return
    console.print(Panel(brief, title=f"📋 {account} — account brief (mental model)", border_style="blue"))
