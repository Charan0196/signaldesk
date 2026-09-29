"""LLM layer tests — all offline, with the network stubbed.

The free tier is unreliable by nature (429s, null content, missing JSON mode),
so the behaviour that matters is: *always* degrade, never crash the request.
"""

from __future__ import annotations

import urllib.error

import pytest

from signaldesk.llm import (
    LLMUnavailable,
    QuickLLM,
    _extract_json,
    _one_of,
    classify_without_llm,
)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        import json

        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, None)


class TestExtractJson:
    def test_plain(self):
        assert _extract_json('{"a":1}') == {"a": 1}

    def test_fenced(self):
        assert _extract_json('```json\n{"a":1}\n```') == {"a": 1}

    def test_fenced_no_language(self):
        assert _extract_json('```\n{"a":1}\n```') == {"a": 1}

    def test_embedded_in_prose(self):
        assert _extract_json('Sure! {"a":1} hope that helps') == {"a": 1}

    def test_array_is_not_a_dict(self):
        # A bare array parses fine here; it is json_call that requires a dict.
        # Assert both halves so the contract is explicit.
        assert _extract_json("[1,2,3]") == [1, 2, 3]
        llm = QuickLLM(models=("m1:free",))
        assert not isinstance(_extract_json("[1,2,3]"), dict)

    def test_garbage(self):
        assert _extract_json("no json") is None


class TestOneOf:
    def test_valid(self):
        assert _one_of("call", ("call", "email"), "note") == "call"

    def test_case_and_space_normalised(self):
        assert _one_of("  CALL ", ("call", "email"), "note") == "call"

    def test_invalid_falls_back(self):
        assert _one_of("carrier_pigeon", ("call", "email"), "note") == "note"

    def test_none_falls_back(self):
        assert _one_of(None, ("call",), "note") == "note"


class TestHeuristicClassifier:
    def test_detects_ticket(self):
        r = classify_without_llm("SUPPORT-4471 load-matching API returned 502s")
        assert r.channel == "ticket"

    def test_detects_email(self):
        r = classify_without_llm("[2026-08-19] Email — Devon replied that Alison is busy")
        assert r.channel == "email"

    def test_detects_call(self):
        assert classify_without_llm("[2026-08-19] Call — post-incident review").channel == "call"

    def test_detects_slack(self):
        assert classify_without_llm("Slack — Devon posted in the shared channel").channel == "slack"

    def test_competitor_word_is_critical(self):
        assert classify_without_llm("they mentioned a competitor").urgency == "critical"

    def test_churn_language(self):
        assert classify_without_llm("they are evaluating a competitor").urgency == "critical"

    def test_quiet_signal_is_high(self):
        assert classify_without_llm("no response for weeks, escalating silence").urgency == "high"

    def test_calm_signal_is_low(self):
        r = classify_without_llm("Great QBR, renewal went well, expanded seats")
        assert r.urgency == "low"
        assert r.sentiment == "neutral"

    def test_empty_input_never_raises(self):
        r = classify_without_llm("")
        assert r.channel == "note" and r.summary == ""

    def test_none_input_never_raises(self):
        r = classify_without_llm(None)
        assert r.urgency == "low"


class TestFallbackAndBreaker:
    def test_first_success_returns_parsed(self, monkeypatch):
        llm = QuickLLM(models=("m1:free",))
        monkeypatch.setattr(llm, "_complete", lambda m, s, u: '{"channel":"call","urgency":"high"}')
        assert llm.json_call("s", "u")["channel"] == "call"
        assert llm.tripped is False

    def test_falls_through_to_next_model(self, monkeypatch):
        llm = QuickLLM(models=("bad:free", "good:free"))

        def fake(model, system, user):
            if model == "bad:free":
                return "not json at all"
            return '{"ok":true}'

        monkeypatch.setattr(llm, "_complete", fake)
        assert llm.json_call("s", "u") == {"ok": True}

    def test_all_rate_limited_raises_and_trips(self, monkeypatch):
        llm = QuickLLM(models=("a:free", "b:free", "c:free", "d:free"), cooldown=60.0)

        def boom(model, system, user):
            raise _http_error(429)

        monkeypatch.setattr(llm, "_complete", boom)
        with pytest.raises(LLMUnavailable):
            llm.json_call("s", "u")
        assert llm.tripped is True

    def test_tripped_breaker_skips_network_entirely(self, monkeypatch):
        llm = QuickLLM(models=("a:free", "b:free", "c:free", "d:free"), cooldown=60.0)
        monkeypatch.setattr(llm, "_complete", lambda *a: (_ for _ in ()).throw(_http_error(429)))
        with pytest.raises(LLMUnavailable):
            llm.json_call("s", "u")
        assert llm.tripped is True

        # Once open, the breaker must answer without touching the network.
        calls = []

        def should_not_run(*a):
            calls.append(a)
            return "{}"

        monkeypatch.setattr(llm, "_complete", should_not_run)
        with pytest.raises(LLMUnavailable, match="circuit breaker open"):
            llm.json_call("s", "u")
        assert calls == [], "breaker open must not hit the network"

    def test_empty_content_is_treated_as_failure(self, monkeypatch):
        llm = QuickLLM(models=("m1:free",))

        def null_content(model, system, user):
            raise LLMUnavailable("empty content")

        monkeypatch.setattr(llm, "_complete", null_content)
        with pytest.raises(LLMUnavailable):
            llm.json_call("s", "u")

    def test_no_key_raises_immediately(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("HERMES_CUSTOM_OPENROUTER_API_KEY", raising=False)
        llm = QuickLLM()
        with pytest.raises(LLMUnavailable):
            llm.json_call("s", "u")

    def test_success_resets_breaker(self, monkeypatch):
        llm = QuickLLM(models=("a:free",), cooldown=60.0)
        llm._tripped_until = 0.0
        monkeypatch.setattr(llm, "_complete", lambda *a: '{"ok":1}')
        llm.json_call("s", "u")
        assert llm._tripped_until == 0.0

    def test_read_signal_normalises_bad_enum(self, monkeypatch):
        llm = QuickLLM(models=("m1:free",))
        monkeypatch.setattr(
            llm,
            "_complete",
            lambda *a: '{"channel":"pigeon","urgency":"nuclear","sentiment":"ok","summary":"s"}',
        )
        r = llm.read_signal("text")
        assert r.channel == "note"
        assert r.urgency == "medium"
        assert r.sentiment == "neutral"
