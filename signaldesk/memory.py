"""The Hindsight memory layer.

This is the only module that talks to Hindsight. It wraps four memory operations
and deliberately does **not** expose ``recall`` — retrieval is reflect's job, so
the agent reasons over memory instead of pattern-matching against it.

    retain            every raw signal becomes a memory unit
    reflect           diagnosis + prescription, with a JSON schema
    mental models     a self-refreshing brief per account
    directives        hard guardrails that apply to every reflect

    observations      enabled on the bank, so facts consolidate into beliefs
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hindsight_client import Hindsight
from pydantic import ValidationError

from .config import Settings
from .schemas import RiskAssessment, SavePlan

log = logging.getLogger("signaldesk.memory")

# --- Bank shaping ---------------------------------------------------------
# Mission/directives shape reflect's reasoning. Writing them carefully is the
# difference between a generic risk summary and one that reasons like a CSM
# who has sat through hundreds of renewals.

MISSION = (
    "You are the retention intelligence for {org}, a B2B software vendor. "
    "Your memory bank holds every customer interaction across every account: call "
    "transcripts, emails, support tickets, QBR notes, and the outcomes of save "
    "plays that were tried. Reason as a seasoned VP of Customer Success who is "
    "defending real recurring revenue. Prefer specific, dated evidence from memory "
    "over generic best practice, and say plainly when memory is thin."
)

RETAIN_MISSION = (
    "Extract durable, decision-relevant facts about customer accounts: who the "
    "champion and economic buyer are, what was promised by our team, commitments "
    "and deadlines given, product/delivery failures, competitor evaluations, "
    "budget or procurement shifts, and the outcome of any save play attempted. "
    "Preserve dates, names, ARR figures, and who said what. Never invent detail."
)

OBSERVATIONS_MISSION = (
    "Track the direction each account is drifting: rising or falling risk, a "
    "champion losing engagement, broken promises we made, and which save plays "
    "have historically rescued accounts with similar failure patterns."
)

DIRECTIVES: list[dict[str, Any]] = [
    {
        "name": "no-invented-evidence",
        "content": (
            "Every claim about an account must be traceable to a memory in this bank. "
            "If a fact (ARR, date, person's name, competitor) was never recorded, say "
            "'not in memory' rather than guessing or using a plausible-sounding value."
        ),
        "priority": 10,
    },
    {
        "name": "prefer-precedent-over-playbook",
        "content": (
            "When choosing a save play, prefer what demonstrably worked on comparable "
            "accounts in this bank over generic SaaS advice. Name the precedent "
            "accounts explicitly."
        ),
        "priority": 8,
    },
    {
        "name": "date-every-signal",
        "content": (
            "Anchor risk judgments to dates from memory and note how stale the most "
            "recent interaction is. An account with no recorded contact in weeks must "
            "be flagged as such rather than assumed healthy."
        ),
        "priority": 6,
    },
]


class MemoryError(RuntimeError):
    """Raised when Hindsight cannot be reached or returns something unusable."""


class MemoryLayer:
    """Thin, typed wrapper around the Hindsight client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.bank = settings.bank_id
        self._client = Hindsight(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=300.0,
            user_agent="signaldesk/0.1",
        )
        self._mental_models: dict[str, str] = {}

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - closing must never raise
            log.debug("client close failed", exc_info=True)

    # -- bank lifecycle ---------------------------------------------------

    def ensure_bank(self) -> dict[str, Any]:
        """Create or reconfigure the memory bank so its reasoning matches our mission."""
        try:
            profile = self._client.create_bank(
                self.bank,
                name="SignalDesk — Churn Signals",
                mission=MISSION.format(org=self.settings.org_name),
                retain_mission=RETAIN_MISSION,
                enable_observations=True,
                observations_mission=OBSERVATIONS_MISSION,
                enable_text_search=True,
                enable_temporal_retrieval=True,
                enable_graph_retrieval=True,
                enable_reranking=True,
                reflect_mission=MISSION.format(org=self.settings.org_name),
            )
            created = True
        except Exception as exc:  # bank already exists -> update it instead
            log.debug("create_bank failed (%s); updating config", exc)
            profile = self._client.update_bank_config(
                self.bank,
                retain_mission=RETAIN_MISSION,
                observations_mission=OBSERVATIONS_MISSION,
                enable_observations=True,
                enable_text_search=True,
                enable_temporal_retrieval=True,
                enable_graph_retrieval=True,
                enable_reranking=True,
                reflect_mission=MISSION.format(org=self.settings.org_name),
            )
            created = False

        self._install_directives()
        return {"bank": self.bank, "created": created, "profile": profile}

    def _install_directives(self) -> None:
        """Guardrails apply to every reflect. Best-effort: never block startup."""
        existing: set[str] = set()
        try:
            for d in items_of(self._client.list_directives(self.bank)):
                name = d.get("name")
                if name:
                    existing.add(name)
        except Exception:  # noqa: BLE001
            log.debug("could not list directives", exc_info=True)

        for spec in DIRECTIVES:
            if spec["name"] in existing:
                continue
            try:
                self._client.create_directive(
                    self.bank,
                    spec["name"],
                    spec["content"],
                    priority=spec["priority"],
                )
            except Exception:  # noqa: BLE001
                log.warning("could not install directive %s", spec["name"], exc_info=True)

    def reset_bank(self) -> None:
        """Wipe memories (used by `demo --reset`) without dropping the config.

        Done over plain HTTP rather than the client's async endpoint: mixing the
        client's sync and async paths leaves an aiohttp session bound to a loop
        that a later `asyncio.run` closes, which fails with "Timeout context
        manager should be used inside a task". A direct DELETE avoids event
        loops entirely.
        """
        url = f"{self.settings.base_url.rstrip('/')}/v1/default/banks/{quote(self.bank)}/memories"
        req = Request(url, method="DELETE")
        req.add_header("Authorization", f"Bearer {self.settings.api_key}")
        try:
            with urlopen(req, timeout=120) as resp:
                resp.read()
        except HTTPError as exc:
            body = exc.read().decode()[:200]
            raise MemoryError(f"reset failed (HTTP {exc.code}): {body}") from exc
        except URLError as exc:
            raise MemoryError(f"reset failed: {exc.reason}") from exc
        self._mental_models.clear()

    # -- retain -----------------------------------------------------------

    def ingest(
        self,
        text: str,
        account: str,
        *,
        channel: str = "note",
        when: datetime | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Write one raw signal into memory, tagged so it stays attributable."""
        # The API takes a string->string map; stringify defensively so a caller
        # passing an int or None fails here with a clear shape, not upstream.
        meta = {"channel": channel}
        for k, v in (metadata or {}).items():
            meta[str(k)] = "" if v is None else str(v)
        resp = self._client.retain(
            self.bank,
            content=text,
            context=f"{account} — {channel}",
            tags=[f"acct:{account}", f"chan:{channel}"],
            metadata=meta,
            timestamp=when or datetime.now(timezone.utc),
        )
        return _as_dict(resp)

    def ingest_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Bulk-load historical signals. items: [{text, account, channel, date, metadata}]."""
        payload = []
        for it in items:
            payload.append(
                {
                    "content": it["text"],
                    "context": f"{it['account']} — {it.get('channel', 'note')}",
                    "tags": [f"acct:{it['account']}", f"chan:{it.get('channel', 'note')}"],
                    "metadata": {"channel": it.get("channel", "note"), **(it.get("metadata") or {})},
                }
            )
        if not payload:
            return {"success": True, "items_count": 0}
        return _as_dict(self._client.retain_batch(self.bank, payload))

    # -- reflect (diagnosis) ---------------------------------------------

    def assess(self, account: str, *, fast: bool = False) -> RiskAssessment:
        """Diagnose one account from every interaction ever recorded on it.

        ``fast=True`` uses Hindsight's low reasoning budget. That is what the
        portfolio board runs — a CSM scanning 20 accounts needs the risk tier
        quickly, not a 20s deliberation per row. Single-account analysis keeps
        the full budget.
        """
        budget = "low" if fast else self.settings.reflect_budget
        query = (
            f"Assess the churn risk for account '{account}' at {self.settings.org_name}.\n\n"
            "Use everything in memory for this account: past calls, emails, tickets, "
            "QBRs, promises we made, delivery failures, procurement moves, and how "
            "the champion has behaved over time. Weigh recent signals more heavily "
            "than old ones, but cite older ones when they explain the current state.\n\n"
            "Return: the 0-100 risk score, the tier, the single primary driver, any "
            "secondary drivers, the champion's status, how many days since the last "
            "recorded interaction, the one event that would most reduce this risk, "
            "and dated evidence quotes for each claim. If memory contains no evidence "
            "for part of this, say so in that field rather than guessing."
        )
        return self._reflect_model(
            query=query,
            model=RiskAssessment,
            budget=budget,
            tags=[f"acct:{account}"],
            fallback_account=account,
        )

    # -- reflect (prescription) ------------------------------------------

    def prescribe(self, assessment: RiskAssessment) -> SavePlan:
        """Choose a save play using what worked on comparable accounts."""
        drivers = "; ".join([assessment.primary_driver, *assessment.drivers]) or "unspecified"
        query = (
            f"Account '{assessment.account}' is at {assessment.risk_tier} risk "
            f"(score {assessment.risk_score}/100). Drivers: {drivers}.\n\n"
            "Recommend the single best save play, using this bank's history rather "
            "than generic advice.\n\n"
            "Search memory for comparable accounts — same failure pattern, similar "
            "segment or ARR band — and check which save plays actually saved them "
            "and which failed. Prefer a play with real precedent here. Then give:\n"
            "  - headline_play: the name of the play\n"
            "  - why_this_play: what memory says about it\n"
            "  - precedent: the specific comparable accounts and what happened\n"
            "  - actions: 2-4 concrete steps, each with an owner, a timeframe in days, "
            "    the reason it works, and the observable signal that it landed\n"
            "  - counter_evidence: when this play has failed before, or what would sink it\n"
            "  - expected_effect: realistic effect on risk if executed well\n\n"
            "If no comparable precedent exists in memory, say so and recommend the "
            "conservative play, with lower confidence."
        )
        return self._reflect_model(
            query=query,
            model=SavePlan,
            budget=self.settings.reflect_budget,
            fallback_account=assessment.account,
        )

    def headline_play(self, assessment: RiskAssessment) -> SavePlan:
        """Cheap one-line play for a board row; full detail comes from :meth:`prescribe`."""
        query = (
            f"Account '{assessment.account}' is at {assessment.risk_tier} risk "
            f"(score {assessment.risk_score}/100). Primary driver: {assessment.primary_driver}. "
            "In ONE short phrase, name the single save play to run first for an account in "
            "exactly this situation, based on comparable accounts in memory. Then one "
            "sentence on why. Do not produce a step-by-step plan."
        )
        return self._reflect_model(
            query=query,
            model=SavePlan,
            budget="low",
            fallback_account=assessment.account,
        )

    def _reflect_model(
        self,
        *,
        query: str,
        model: type,
        budget: str,
        tags: list[str] | None = None,
        fallback_account: str,
    ):
        """Call reflect with a JSON schema and validate the result into `model`."""
        try:
            resp = self._client.reflect(
                self.bank,
                query,
                budget=budget,
                response_schema=model.model_json_schema(),
                tags=tags,
                tags_match="any",
                include_facts=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise MemoryError(f"reflect failed: {exc}") from exc

        data = _as_dict(resp)
        payload = data.get("structured_output")
        if payload is None:
            # Some backends return the object as text; salvage it before failing.
            payload = _coerce_json(data.get("text"))
        if payload is None:
            raise MemoryError("reflect returned no structured output")
        if isinstance(payload, str):
            payload = _coerce_json(payload)
        if not isinstance(payload, dict):
            raise MemoryError(f"reflect returned unexpected payload type: {type(payload)}")

        payload.setdefault("account", fallback_account)
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            log.warning("schema validation failed for %s: %s", model.__name__, exc)
            raise MemoryError(f"reflect output failed validation: {exc}") from exc

    # -- mental models ----------------------------------------------------

    def refresh_brief(self, account: str) -> str:
        """Create or refresh this account's self-updating brief. Returns model id.

        The in-process id cache is only a fast path — after a restart it is
        empty, so we look the model up by name before creating anything.
        Without that, every restart appends another duplicate brief.
        """
        mm_id = self._mental_models.get(account) or self._find_brief_id(account)
        query = (
            f"Write the standing brief for account '{account}': who the key people are "
            "and their roles, what we promised and by when, current delivery status, "
            "open commitments, and the current risk posture. This brief is re-read "
            "before every save play, so it must be current and specific."
        )
        if mm_id:
            try:
                self._client.refresh_mental_model(self.bank, mm_id)
                self._mental_models[account] = mm_id
                return mm_id
            except Exception:  # noqa: BLE001 - stale id; fall through and recreate
                log.warning("refresh of %s failed; recreating", mm_id, exc_info=True)
                self._mental_models.pop(account, None)
        try:
            created = self._client.create_mental_model(
                self.bank,
                name=f"{account} — account brief",
                source_query=query,
                tags=[f"acct:{account}"],
            )
        except Exception as exc:  # noqa: BLE001
            raise MemoryError(f"mental model creation failed for {account}: {exc}") from exc
        new_id = _as_dict(created).get("mental_model_id")
        if new_id:
            self._mental_models[account] = new_id
        return new_id or ""

    def _find_brief_id(self, account: str) -> str | None:
        """Locate this account's existing brief by name, newest wins."""
        prefix = f"{account} — account brief"
        try:
            items = items_of(self._client.list_mental_models(self.bank, limit=200))
        except Exception:  # noqa: BLE001
            log.debug("could not list mental models", exc_info=True)
            return None
        matches = [i for i in items if str(i.get("name", "")).startswith(prefix)]
        if not matches:
            return None
        matches.sort(key=lambda i: str(i.get("created_at") or ""), reverse=True)
        return matches[0].get("id")

    def get_brief(self, account: str) -> str | None:
        """Read the stored brief text for an account, if one has been built.

        Mental-model ``content`` is sometimes a plain string and sometimes a
        structured block tree (``{"heading": ..., "blocks": [...]}``), so both
        shapes are flattened to readable text here.
        """
        mm_id = self._mental_models.get(account)
        try:
            items = items_of(self._client.list_mental_models(self.bank, detail="full"))
        except Exception:  # noqa: BLE001
            log.debug("could not list mental models", exc_info=True)
            return None
        for it in items:
            iid = it.get("id")
            name = it.get("name", "")
            if (mm_id and iid == mm_id) or name.startswith(f"{account} —"):
                if mm_id is None and iid:
                    self._mental_models[account] = iid
                text = _flatten_content(it.get("content"))
                return text or None
        return None

    # -- introspection ----------------------------------------------------

    def known_accounts(self) -> list[str]:
        """Accounts present in memory, discovered from tags (no recall involved).

        Raises rather than returning [] on failure: a swallowed error here makes
        an empty portfolio look identical to a working one, and a CSM who sees
        "no risk anywhere" instead of "couldn't reach memory" acts on it.
        """
        page = _as_dict(self._client.list_memories(self.bank, limit=1000))
        accounts: dict[str, int] = {}
        for item in page.get("items", []):
            for tag in item.get("tags") or []:
                if isinstance(tag, str) and tag.startswith("acct:"):
                    name = tag[5:]
                    accounts[name] = accounts.get(name, 0) + 1
        return [a for a, _ in sorted(accounts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def stats(self) -> dict[str, Any]:
        """Counts for the demo's 'memory is filling up' moment."""
        out: dict[str, Any] = {"bank": self.bank}
        try:
            page = _as_dict(self._client.list_memories(self.bank, limit=1))
            out["memories"] = page.get("total", 0)
        except Exception:  # noqa: BLE001
            out["memories"] = None
        for label, fn in (
            ("mental_models", lambda: len(items_of(self._client.list_mental_models(self.bank)))),
            ("directives", lambda: len(items_of(self._client.list_directives(self.bank)))),
        ):
            try:
                out[label] = fn()
            except Exception:  # noqa: BLE001
                out[label] = None
        out["accounts"] = len(self.known_accounts())
        return out


# --- helpers ---------------------------------------------------------------


def run_sync(awaitable):
    """Run one of the client's coroutine APIs from sync code.

    Several `memory.*` endpoints are async while the rest of the client is sync.
    This keeps the call sites readable and, crucially, makes an un-awaited
    coroutine a loud error instead of a silent no-op.
    """
    import asyncio
    import inspect as _inspect

def run_sync(awaitable):
    """Run one of the client's coroutine APIs from sync code.

    Several `memory.*` endpoints are async while the rest of the client is sync.
    `asyncio.run` is correct here: it builds a fresh loop per call, and the
    client's aiohttp session is created per-request rather than cached, so
    reusing a long-lived loop instead leaves it closed by a previous call and
    fails with "Event loop is closed".

    Keeping this in one place also turns an un-awaited coroutine into a loud
    error instead of a silent no-op.
    """
    import asyncio
    import inspect as _inspect

    if not _inspect.isawaitable(awaitable):
        return awaitable
    return asyncio.run(_as_coro(awaitable))


def _as_coro(awaitable):
    async def _wrap():
        return await awaitable

    return _wrap()


def _flatten_content(node: Any, depth: int = 0) -> str:
    """Render mental-model content as readable text.

    Hindsight returns brief content either as a markdown string or as a nested
    block tree of ``{"heading", "level", "blocks"}`` nodes. The UI wants prose.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        parts = [_flatten_content(n, depth) for n in node]
        return "\n\n".join(_dedupe(parts))
    if isinstance(node, dict):
        parts: list[str] = []
        heading = node.get("heading") or node.get("title")
        if heading:
            parts.append(f"{'#' * int(node.get('level') or depth + 2)} {heading}".strip())
        for key in ("text", "content", "body", "markdown"):
            val = node.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        for key in ("blocks", "children", "items", "sections"):
            if isinstance(node.get(key), (list, dict)):
                parts.append(_flatten_content(node[key], depth + 1))
        return "\n\n".join(_dedupe(parts))
    return str(node)


def _dedupe(parts: list[str]) -> list[str]:
    """Drop repeats while preserving order.

    Consolidation can emit the same block more than once; a brief that repeats
    itself reads like a bug to whoever has to act on it.
    """
    seen, out = set(), []
    for p in parts:
        key = p.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                continue
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")} if hasattr(obj, "__dict__") else {}


def items_of(obj: Any) -> list[dict[str, Any]]:
    """Pull the record list out of a list response.

    List endpoints wrap their payload as ``{"items": [...]}`` (sometimes with
    ``total``/``limit``/``offset`` alongside), so every consumer needs the same
    unwrap — getting it wrong silently yields an empty UI.
    """
    data = _as_dict(obj)
    items = data.get("items")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


def _coerce_json(text: Any) -> Any:
    """Pull a JSON object out of a text blob (handles ```json fences)."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(cleaned[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
