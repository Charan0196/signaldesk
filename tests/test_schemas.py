"""Contract hardening tests — these cover the shapes that actually break in prod.

Run: .venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

from signaldesk.schemas import (
    Evidence,
    RiskAssessment,
    SaveAction,
    SavePlan,
    tier_from_score,
)


class TestEvidenceCoercion:
    def test_string_evidence_is_salvaged(self):
        ev = Evidence.model_validate("Devon Achebe went quiet after the incident review.")
        assert ev.quote.startswith("Devon Achebe")
        assert ev.date == "undated"

    def test_leading_date_is_extracted(self):
        ev = Evidence.model_validate("2026-08-04 — procurement threatened to leave")
        assert ev.date == "2026-08-04"
        assert "procurement" in ev.quote

    def test_dict_evidence_passes_through(self):
        ev = Evidence.model_validate({"date": "2026-01-01", "source": "call", "quote": "hi"})
        assert ev.source == "call"


class TestRiskAssessmentTolerance:
    def test_string_evidence_array(self):
        a = RiskAssessment.model_validate(
            {
                "account": "Meridian Freight",
                "risk_tier": "high",
                "risk_score": 68,
                "primary_driver": "Economic buyer has no relationship with us.",
                "evidence": ["2026-09-08 — Freightwise quoted 15% lower"],
            }
        )
        assert a.risk_score == 68
        assert a.evidence[0].date == "2026-09-08"

    def test_unrecognized_tier_falls_back_to_score(self):
        a = RiskAssessment.model_validate(
            {
                "account": "X",
                "risk_tier": "SEVERE",
                "risk_score": 91,
                "primary_driver": "d",
            }
        )
        assert a.risk_tier == "critical"

    def test_fractional_score_recovers_tier(self):
        a = RiskAssessment.model_validate(
            {"account": "X", "risk_tier": "?", "risk_score": 0.55, "primary_driver": "d"}
        )
        assert a.risk_tier == "high"
        assert a.risk_score == 55

    def test_silent_days_scraped_from_prose(self):
        a = RiskAssessment.model_validate(
            {
                "account": "X",
                "risk_tier": "high",
                "risk_score": 60,
                "primary_driver": "Champion has not replied in 43 days.",
            }
        )
        assert a.silent_days == 43

    def test_silent_days_as_string(self):
        """Models answer '300 days' or 'Not applicable' — neither may crash triage."""
        a = RiskAssessment.model_validate(
            {
                "account": "X",
                "risk_tier": "high",
                "risk_score": 60,
                "primary_driver": "d",
                "silent_days": "300 days",
            }
        )
        assert a.silent_days == 300

    def test_silent_days_not_applicable_becomes_none(self):
        a = RiskAssessment.model_validate(
            {
                "account": "X",
                "risk_tier": "high",
                "risk_score": 60,
                "primary_driver": "d",
                "silent_days": "Not applicable",
            }
        )
        assert a.silent_days is None

    def test_flat_string_drivers_split(self):
        a = RiskAssessment.model_validate(
            {
                "account": "X",
                "risk_tier": "high",
                "risk_score": 60,
                "primary_driver": "d",
                "drivers": "- three incidents\n- competitor in building",
            }
        )
        assert len(a.drivers) == 2


class TestSavePlanTolerance:
    def test_string_action_becomes_object(self):
        plan = SavePlan.model_validate(
            {
                "account": "X",
                "headline_play": "Exec sponsor alignment",
                "why_this_play": "worked on Halcyon",
                "actions": ["Get the CFO on a call"],
            }
        )
        assert plan.actions[0].action == "Get the CFO on a call"
        assert plan.actions[0].within_days is None  # unknown, not invented

    def test_within_days_parses_from_text(self):
        act = SaveAction.model_validate({"action": "Send summary", "within_days": "within 14 days"})
        assert act.within_days == 14

    def test_precedent_flat_string(self):
        plan = SavePlan.model_validate(
            {
                "account": "X",
                "headline_play": "p",
                "why_this_play": "w",
                "precedent": "Halcyon Logistics; Ironvale Manufacturing",
            }
        )
        assert len(plan.precedent) == 2

    def test_bad_confidence_degrades_to_medium(self):
        plan = SavePlan.model_validate(
            {"account": "X", "headline_play": "p", "why_this_play": "w", "confidence": "pretty sure"}
        )
        assert plan.confidence == "medium"


def test_tier_boundaries():
    assert tier_from_score(74) == "high"
    assert tier_from_score(75) == "critical"
    assert tier_from_score(49) == "medium"
    assert tier_from_score(24) == "low"


class TestActionUnpacking:
    """Models pack owner/timeframe into the action string; we must recover them."""

    def test_parenthesised_owner_and_days(self):
        act = SaveAction.model_validate(
            {"action": "Draft Reliability Memo (Owner: SRE Lead, 3 days)"}
        )
        assert act.owner == "SRE Lead"
        assert act.within_days == 3
        assert "Memo" in act.action and "Owner" not in act.action

    def test_parenthesised_owner_only(self):
        act = SaveAction.model_validate({"action": "Book exec review (Owner: VP Customer Success)"})
        assert act.owner == "VP Customer Success"
        assert act.within_days is None  # genuinely unknown, not a fake "day 7"

    def test_dash_separated_owner_and_days(self):
        act = SaveAction.model_validate({"action": "Send the ROI summary — CFO, 5 days"})
        assert act.owner == "CFO"
        assert act.within_days == 5
        assert act.action == "Send the ROI summary"

    def test_explicit_fields_win_over_text(self):
        act = SaveAction.model_validate(
            {"action": "Call (Owner: Intern, 1 day)", "owner": "CSM", "within_days": 10}
        )
        assert act.owner == "CSM"
        assert act.within_days == 10

    def test_plain_action_is_untouched(self):
        act = SaveAction.model_validate({"action": "Schedule the reliability review"})
        assert act.owner == ""
        assert act.within_days is None
        assert act.success_signal == ""
        assert act.action == "Schedule the reliability review"

    def test_owner_inferred_from_role_words(self):
        act = SaveAction.model_validate({"action": "Escalate to the engineering team for a fix"})
        assert "engineering" in act.owner.lower()

    def test_string_days_are_parsed(self):
        act = SaveAction.model_validate({"action": "Ship summary", "within_days": "within 21 days"})
        assert act.within_days == 21


def test_schemas_are_valid_json_schema():
    """The wire format handed to reflect must be a usable JSON Schema."""
    RiskAssessment.model_json_schema()
    SavePlan.model_json_schema()
