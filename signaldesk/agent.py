"""The SignalDesk agent.

One job: take a raw customer signal, and return a grounded answer to
"are they leaving, why, and what do I do about it today?"

    ingest()  -> write the signal to memory, update the account brief
    analyze() -> diagnose the account, then prescribe a save play

The agent holds no account logic of its own. Every judgment comes from
``reflect`` over the memory bank, which is what makes the agent improve as the
bank grows: the save plays get sharper because the precedent set gets richer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .config import Settings
from .memory import MemoryError, MemoryLayer
from .schemas import RiskAssessment, SavePlan

log = logging.getLogger("signaldesk.agent")


@dataclass
class Finding:
    """The complete answer for one account, with the brief that grounded it."""

    account: str
    assessment: RiskAssessment
    plan: SavePlan
    brief: str | None = None
    mental_model_id: str | None = None
    ingested: int = 0

    def headline(self) -> str:
        tier = self.assessment.risk_tier.upper()
        return f"{self.account}: {tier} ({self.assessment.risk_score}/100) — {self.assessment.primary_driver}"

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "assessment": self.assessment.model_dump(),
            "save_plan": self.plan.model_dump(),
            "brief": self.brief,
            "mental_model_id": self.mental_model_id,
            "signals_ingested": self.ingested,
        }


class SignalDeskAgent:
    """Coordinates memory writes and memory-grounded reasoning."""

    def __init__(self, settings: Settings | None = None, memory: MemoryLayer | None = None):
        self.settings = settings or Settings.from_env()
        self.memory = memory or MemoryLayer(self.settings)

    def close(self) -> None:
        self.memory.close()

    def __enter__(self) -> "SignalDeskAgent":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def setup(self) -> dict:
        """Create/configure the bank and its guardrails."""
        return self.memory.ensure_bank()

    def ingest(
        self,
        text: str,
        account: str,
        *,
        channel: str = "note",
        when: datetime | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict:
        """Record one new signal. Raises ValueError on empty input."""
        clean = text.strip()
        if not clean:
            raise ValueError("signal text is empty")
        return self.memory.ingest(
            clean, account=account, channel=channel, when=when, metadata=metadata
        )

    def ingest_history(self, records: list[dict]) -> dict:
        """Bulk-load historical signals: [{text, account, channel, date, metadata}]."""
        return self.memory.ingest_batch(records)

    def analyze(self, account: str, *, refresh_brief: bool = True) -> Finding:
        """Full pipeline: refresh the brief, diagnose, prescribe."""
        mm_id = None
        brief = None
        if refresh_brief:
            try:
                mm_id = self.memory.refresh_brief(account) or None
                brief = self.memory.get_brief(account)
            except MemoryError:
                log.warning("brief refresh failed for %s; continuing", account, exc_info=True)

        assessment = self.memory.assess(account)
        plan = self.memory.prescribe(assessment)
        return Finding(
            account=account,
            assessment=assessment,
            plan=plan,
            brief=brief,
            mental_model_id=mm_id,
        )

    def triage(self, accounts: list[str]) -> list[Finding]:
        """Analyze several accounts, ordered highest risk first.

        Runs the fast diagnosis (low reasoning budget, no mental-model refresh)
        so a 20-account portfolio stays interactive. Clicking a row re-runs the
        full-depth analysis for that one account.

        One account failing does not sink the board — the error becomes a
        visible placeholder so the operator still sees the rest of the book.
        """
        findings: list[Finding] = []
        for acct in accounts:
            try:
                assessment = self.memory.assess(acct, fast=True)
                findings.append(
                    Finding(
                        account=acct,
                        assessment=assessment,
                        plan=self.memory.headline_play(assessment),
                    )
                )
            except (MemoryError, ValueError) as exc:
                log.error("triage failed for %s: %s", acct, exc)
                findings.append(_failed_finding(acct, str(exc)))
        findings.sort(key=lambda f: f.assessment.risk_score, reverse=True)
        return findings


def _failed_finding(account: str, error: str) -> Finding:
    """A visible failure beats a silent gap in the triage board."""
    return Finding(
        account=account,
        assessment=RiskAssessment(
            account=account,
            risk_tier="low",
            risk_score=0,
            confidence="low",
            primary_driver=f"analysis failed: {error}",
            reasoning="No verdict could be produced; treat as unknown, not as safe.",
        ),
        plan=SavePlan(
            account=account,
            headline_play="none — analysis failed",
            why_this_play=error,
            confidence="low",
        ),
    )
