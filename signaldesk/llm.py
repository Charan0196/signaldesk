"""Optional OpenRouter layer for fast, cheap side-tasks.

Hindsight ``reflect`` remains the reasoning engine for anything that touches
account history — that is the product. This module handles the work that does
NOT need memory and would otherwise make the UI feel slow:

    classifying an incoming signal (channel, sentiment, urgency)
    producing a one-line portfolio summary of a raw transcript
    normalizing pasted text into a clean signal before ``retain``

Design constraints, learned from probing the live free tier:

* The ``:free`` models are **frequently 429'd or return ``None`` content**.
  Every call therefore walks a fallback list and treats the LLM as optional —
  a failure degrades to a heuristic, never to a broken request.
* JSON mode is **not** universally supported, so we ask for JSON in the prompt
  and parse defensively rather than sending ``response_format``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("signaldesk.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Ordered best-first. Verified to answer with clean JSON on the free tier.
# The ``:free`` pool rotates constantly, so the list is deliberately wide and
# every entry is treated as "try it, fall through on 429".
DEFAULT_MODELS: tuple[str, ...] = (
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "poolside/laguna-xs-2.1:free",
    "google/gemma-4-26b-a4b-it:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3.5-lightning:free",
)

CHANNELS = ("call", "email", "ticket", "qbr", "slack", "note")


class LLMUnavailable(RuntimeError):
    """No free model answered. Callers must fall back, not crash."""


@dataclass
class SignalReading:
    """What a quick LLM pass can tell us about an incoming raw signal."""

    channel: str
    urgency: str
    sentiment: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "urgency": self.urgency,
            "sentiment": self.sentiment,
            "summary": self.summary,
        }


def _api_key() -> str | None:
    for name in ("OPENROUTER_API_KEY", "HERMES_CUSTOM_OPENROUTER_API_KEY"):
        v = os.environ.get(name)
        if v:
            return v.strip()
    return None


def available() -> bool:
    return _api_key() is not None


# One shared client so the breaker state (and its cooldown) is global: a
# saturated free tier should stop costing latency across every request, not
# just the one that happened to discover it.
_SHARED: "QuickLLM | None" = None


def shared() -> "QuickLLM":
    global _SHARED
    if _SHARED is None:
        _SHARED = QuickLLM()
    return _SHARED


class QuickLLM:
    """Thin OpenRouter client with model fallback and a hard availability check."""

    def __init__(
        self,
        models: tuple[str, ...] = DEFAULT_MODELS,
        timeout: float = 25.0,
        max_tokens: int = 320,
        cooldown: float = 120.0,
    ):
        self.models = models
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.cooldown = cooldown
        self._disabled = not available()
        # When the free tier is saturated, every call would otherwise walk the
        # whole model list and stall. Trip a breaker and go straight to the
        # heuristic path instead.
        self._tripped_until = 0.0

    @property
    def tripped(self) -> bool:
        return time.monotonic() < self._tripped_until

    # -- low level --------------------------------------------------------

    def _complete(self, model: str, system: str, user: str) -> str:
        key = _api_key()
        if not key:
            raise LLMUnavailable("no OPENROUTER_API_KEY configured")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8420",
                "X-Title": "SignalDesk",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        choices = data.get("choices")
        if not choices:
            raise LLMUnavailable(f"{model}: no choices in response")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            # Free models intermittently return a null message.
            raise LLMUnavailable(f"{model}: empty content")
        return content

    def json_call(self, system: str, user: str) -> dict[str, Any]:
        """Ask for JSON, trying each model in turn. Raises if all fail.

        Only a wall of rate limits trips the breaker — a single bad prompt
        should not disable the LLM path for minutes.
        """
        if self._disabled:
            raise LLMUnavailable("LLM layer disabled (no API key)")
        if self.tripped:
            raise LLMUnavailable("circuit breaker open (free tier saturated)")

        errors: list[str] = []
        rate_limited = 0
        for model in self.models:
            try:
                raw = self._complete(model, system, user)
                parsed = _extract_json(raw)
                if isinstance(parsed, dict):
                    self._tripped_until = 0.0
                    return parsed
                errors.append(f"{model}: not a JSON object")
            except urllib.error.HTTPError as exc:
                errors.append(f"{model}: HTTP {exc.code}")
                if exc.code in (429, 503):
                    rate_limited += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {type(exc).__name__}")

            # Only keep trying while models are merely unavailable.
            if rate_limited and rate_limited >= 3:
                self._tripped_until = time.monotonic() + self.cooldown
                break
            time.sleep(0.4)  # be polite to a shared, rate-limited free tier

        raise LLMUnavailable("; ".join(errors))

    # -- task: classify an incoming signal --------------------------------

    def read_signal(self, text: str) -> SignalReading:
        """Fast structured read of a raw signal. Raises if unavailable."""
        snippet = text.strip()[:1500]
        data = self.json_call(
            system=(
                "You classify customer-success signals. Reply with ONLY a JSON object "
                'with keys: channel (one of call|email|ticket|qbr|slack|note), '
                'urgency (low|medium|high|critical), sentiment (positive|neutral|negative), '
                "summary (one sentence, max 20 words). No prose, no code fences."
            ),
            user=f"Signal:\n{snippet}",
        )
        return SignalReading(
            channel=_one_of(data.get("channel"), CHANNELS, "note"),
            urgency=_one_of(data.get("urgency"), ("low", "medium", "high", "critical"), "medium"),
            sentiment=_one_of(data.get("sentiment"), ("positive", "neutral", "negative"), "neutral"),
            summary=str(data.get("summary") or "").strip()[:240],
        )


# --- helpers ---------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)


def _extract_json(raw: str) -> Any:
    """Pull a JSON object out of a model response, fences and all."""
    text = raw.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _one_of(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


# --- heuristics: the no-LLM path -----------------------------------------

_RISK_TERMS = {
    "critical": ("churn", "terminate", "cancellation", "competitor", "evaluating", "not renewing"),
    "high": ("escalat", "unhappy", "delay", "refund", "budget cut", "silent"),
}


# Ordered by specificity. An explicit channel prefix in the text wins outright,
# so "[2026-08-19] Call — post-incident review" stays a call even though it
# contains the word "incident".
_CHANNELS_EXPLICIT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ticket", ("ticket —", "ticket -", "support-", "ticket-")),
    ("qbr", ("qbr —", "qbr -")),
    ("email", ("email —", "email -")),
    ("call", ("call —", "call -")),
    ("slack", ("slack —", "slack -")),
)
_CHANNELS_HINT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ticket", ("ticket-", "support-", "incident", "outage", "sev1")),
    ("email", ("subject:", "wrote:", "replied", "forwarded")),
    ("slack", ("slack", "posted in the shared channel", "in the channel")),
    ("call", ("call —", "spoke", "on the call", "qbr")),
)


def classify_without_llm(text: str) -> SignalReading:
    """Rule-based fallback so the feature still works with no key or no free slot."""
    low = (text or "").lower()
    head = low[:80]  # channel tags only ever appear at the head of a signal

    channel = "note"
    for name, markers in _CHANNELS_EXPLICIT:
        if any(m in head for m in markers):
            channel = name
            break
    else:
        for name, markers in _CHANNELS_HINT:
            if any(m in low for m in markers):
                channel = name
                break

    urgency = "low"
    for level in ("critical", "high"):
        if any(t in low for t in _RISK_TERMS[level]):
            urgency = level
            break

    sentiment = "negative" if urgency in ("critical", "high") else "neutral"
    first = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    summary = re.sub(r"\s+", " ", first).strip(" —-[]")[:200]
    return SignalReading(channel=channel, urgency=urgency, sentiment=sentiment, summary=summary)


def read_signal_safely(text: str) -> tuple[SignalReading, str]:
    """Always returns a reading plus the source: ``llm`` or ``heuristic``."""
    try:
        if available():
            return shared().read_signal(text), "llm"
    except LLMUnavailable as exc:
        log.info("LLM unavailable (%s), using heuristics", exc)
    return classify_without_llm(text), "heuristic"
