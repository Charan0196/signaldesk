"""Typed contracts for what the agent produces.

Two ideas carry the whole product:

* ``RiskAssessment`` — the *diagnosis*: is this account drifting, and why.
* ``SavePlan``      — the *prescription*: what to actually do, and why that
  worked before on accounts like this one.

Both are produced by Hindsight ``reflect`` under a JSON schema, then validated
into these models. Validation is the agent's only guardrail against a model
inventing fields the memory does not support.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RiskTier = Literal["critical", "high", "medium", "low"]

TIER_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


class Evidence(BaseModel):
    """A single cited memory backing a claim.

    Models asked for an array of objects sometimes return plain strings instead.
    We accept both rather than failing the whole assessment over formatting —
    a citation that came back as a sentence is still worth showing.
    """

    date: str = Field(description="When the signal happened, from the memory")
    source: str = Field(description="Which channel/document the signal came from")
    quote: str = Field(description="Short verbatim quote from the memory")
    supports: str = Field(
        default="", description="Which part of the diagnosis this evidence supports"
    )

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value):
        """Salvage a bare string citation into a usable Evidence object."""
        if isinstance(value, str):
            text = value.strip()
            # Pull a leading date out of "2026-08-04 — the thing" style strings.
            date = "undated"
            m = re.match(r"^\[?(\d{4}-\d{2}-\d{2})\]?[\s—–:-]+(.*)$", text)
            if m:
                date, text = m.group(1), m.group(2).strip()
            return {"date": date, "source": "memory", "quote": text}
        return value


class RiskAssessment(BaseModel):
    """The agent's read on one account, grounded in every prior interaction."""

    account: str
    risk_tier: RiskTier
    risk_score: int = Field(ge=0, le=100, description="0 = safe, 100 = certain to churn")
    confidence: Literal["high", "medium", "low"] = "medium"
    primary_driver: str = Field(
        description="The single biggest reason this account is at risk, in one sentence"
    )
    drivers: list[str] = Field(default_factory=list, description="Secondary contributing drivers")
    evidence: list[Evidence] = Field(default_factory=list)
    champion_status: str = Field(
        default="unknown", description="Champion engaged / weakened / gone / unknown"
    )
    silent_days: int | None = Field(
        default=None, description="Days since last recorded interaction, if known"
    )
    what_would_change_this: str = Field(
        default="", description="The single event that would most reduce this risk"
    )
    reasoning: str = Field(
        default="", description="Short prose walkthrough of how memory led to this verdict"
    )

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, value):
        """Normalize the shapes models actually return.

        Two drift cases bite in practice: a tier that isn't one of our four
        literals, and evidence/driver arrays that arrive as one flat string.
        """
        if not isinstance(value, dict):
            return value
        v = dict(value)

        # Normalize the score itself: "82", 0.82, 82.4 all mean the same thing.
        score = _coerce_score(v.get("risk_score"))
        v["risk_score"] = score

        tier = str(v.get("risk_tier", "")).strip().lower()
        v["risk_tier"] = tier if tier in TIER_RANK else tier_from_score(score)

        v["evidence"] = _as_list(v.get("evidence"))
        v["drivers"] = _as_str_list(v.get("drivers"))

        # "no contact in 43 days" / "43 days ago" -> silent_days
        if v.get("silent_days") in (None, "", "unknown") or isinstance(v.get("silent_days"), str):
            v["silent_days"] = _scrape_days(
                " ".join(
                    str(v.get(k, ""))
                    for k in ("silent_days", "reasoning", "champion_status", "primary_driver")
                )
            )
        return v


class SaveAction(BaseModel):
    """One concrete, owned step in the rescue plan.

    ``owner``, ``within_days`` and ``success_signal`` are optional on purpose.
    When the model does not supply them we return empty/None instead of a
    confident-looking default — a plan that says "CSM, day 7" for every step
    looks complete but tells the rep nothing. The renderer shows the gap.
    """

    action: str
    owner: str = Field(default="", description="Who runs it, e.g. 'SRE Lead'")
    within_days: int | None = Field(
        default=None, ge=0, le=90, description="Days from today to run this step"
    )
    rationale: str = Field(
        default="", description="Why this worked on comparable accounts in memory"
    )
    success_signal: str = Field(
        default="", description="The observable event that means this step landed"
    )

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, value):
        if isinstance(value, str):
            return {"action": value}
        if not isinstance(value, dict):
            return value
        v = dict(value)
        # "within 14 days" / "day 14" / "2 weeks"
        if isinstance(v.get("within_days"), str):
            m = re.search(r"(\d+)", v["within_days"])
            v["within_days"] = int(m.group(1)) if m else None
        # A step with no action text is unusable — fall back to its own rationale.
        if not str(v.get("action", "")).strip():
            v["action"] = str(v.get("rationale") or v.get("description") or "").strip()
        return _unpack_action(v)


_TRAILING_META = re.compile(
    r"\(\s*owner\s*:\s*(?P<who>[^)]+?)\s*"
    r"(?:[,;]\s*(?P<days>\d{1,3})\s*(?:days?|wks?|weeks?)\b)?\s*\)\s*$",
    re.I,
)
_INNER_META = re.compile(r"^(?P<body>.*?)\s*[—–-]\s*(?:owner\s*:\s*)?(?P<who>[^,;(]{2,40})\s*,\s*(?P<days>\d{1,3})\s*days?\b", re.I)
_ROLE_HINT = re.compile(
    r"\b(csm|account manager|am|sre|support|engineer|engineering|vp|vpe|ceo|cfo|cto|"
    r"cro|customer success|success manager|exec|executive|sales)\b",
    re.I,
)


def _unpack_action(v: dict) -> dict:
    """Pull owner and timeframe out of the action text when the model packs them in.

    Models frequently return ``"Draft the memo (Owner: SRE Lead, 3 days)"`` and
    leave the structured fields at their defaults. Recovering them keeps the plan
    actionable instead of leaving every step owned by "CSM, day 7".
    """
    action = str(v.get("action", "") or "")
    if not action:
        return v

    owner = v.get("owner")
    days = v.get("within_days")
    days_int = isinstance(days, int) and not isinstance(days, bool)
    parsed_owner = False

    m = _TRAILING_META.search(action)
    if m:
        owner = owner or m.group("who").strip()
        if not days_int and m.group("days"):
            days = int(m.group("days"))
        action = action[: m.start()].strip(" —–-([")
        v["action"] = action
        parsed_owner = bool(owner)
    else:
        m2 = _INNER_META.match(action)
        if m2:
            if not owner and _ROLE_HINT.search(m2.group("who")):
                owner = m2.group("who").strip()
                parsed_owner = True
                if not days_int:
                    days = int(m2.group("days"))
                v["action"] = m2.group("body").strip(" —–-(")

    # Only guess an owner from role words when nothing explicit was supplied —
    # never override a value the model (or the text) actually specified.
    if not parsed_owner and (not owner or owner == "CSM"):
        guess = _ROLE_HINT.search(action)
        if guess and guess.group(1).lower() != "csm":
            owner = guess.group(1)
    if owner:
        v["owner"] = owner
    if not days_int and isinstance(days, (int, float)):
        v["within_days"] = int(days)
    return v


class SavePlan(BaseModel):
    """The prescription, derived from how comparable accounts actually turned out."""

    account: str
    headline_play: str = Field(description="The name of the single recommended play")
    why_this_play: str = Field(
        description="Why this play, citing what memory shows about similar accounts"
    )
    confidence: Literal["high", "medium", "low"] = "medium"
    actions: list[SaveAction] = Field(default_factory=list)
    precedent: list[str] = Field(
        default_factory=list,
        description="Comparable accounts from memory that this play worked on",
    )
    counter_evidence: str = Field(
        default="",
        description="When this play has failed before, or what would sink it",
    )
    expected_effect: str = Field(
        default="", description="Realistic effect on risk if the plan is executed"
    )

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, value):
        if not isinstance(value, dict):
            return value
        v = dict(value)
        v["actions"] = _as_list(v.get("actions"))
        v["precedent"] = _as_str_list(v.get("precedent"))
        conf = str(v.get("confidence", "")).strip().lower()
        v["confidence"] = conf if conf in ("high", "medium", "low") else "medium"
        return v


class AccountSnapshot(BaseModel):
    """Everything SignalDesk holds about one account — used by the `brief` command."""

    account: str
    memory_model_id: str | None = None
    last_refreshed: str | None = None


def tier_from_score(score: int) -> RiskTier:
    """Map a 0-100 risk score onto a tier, so score and tier can never disagree."""

    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _coerce_score(value: Any) -> int:
    """Normalize a risk score to a 0-100 int.

    Models variously return ``82``, ``"82"``, ``82.4``, ``0.82`` or ``"8/10"``.
    Anything unreadable becomes 50 (medium) rather than crashing the pipeline.
    """
    if isinstance(value, str):
        m = re.search(r"(\d+(?:\.\d+)?)", value)
        if not m:
            return 50
        n = float(m.group(1))
        # "8/10" style -> scale to 100
        if "/" in value and value.count("/") == 1:
            try:
                denom = float(value.split("/")[1])
                n = n / denom * 100 if denom else n
            except (ValueError, ZeroDivisionError):
                pass
    else:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 50
    if 0 < n <= 1.0:  # a model returning 0.85 for "85"
        n *= 100
    return int(max(0, min(100, round(n))))


def _as_list(value: Any) -> list:
    """Coerce a value to a list, splitting a single string on bullets/newlines."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        parts = [p.strip(" -•\t") for p in re.split(r"[\n;]+|(?:^|\s)[-•]\s+", value) if p.strip(" -•\t")]
        return parts or [value.strip()]
    return [value]


def _as_str_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            # some models return [{driver: "..."}]
            out.append(str(next(iter(item.values()), "")).strip())
        else:
            out.append(str(item).strip())
    return [o for o in out if o]


def _scrape_days(text: str) -> int | None:
    """Find '43 days' in prose when silent_days wasn't given as a number."""
    m = re.search(r"(\d{1,3})\s*days?\b", text, re.I)
    return int(m.group(1)) if m else None


# JSON schemas handed to Hindsight `reflect` (response_schema). Derived from the
# models above so the wire format can never drift from the Python contract.
def risk_assessment_schema() -> dict:
    return RiskAssessment.model_json_schema()


def save_plan_schema() -> dict:
    return SavePlan.model_json_schema()
