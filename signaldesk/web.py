"""FastAPI backend for SignalDesk.

Every agent capability the CLI has is exposed as JSON, so the dashboard drives
the *same* agent rather than a parallel reimplementation of it:

    GET  /api/state                  bank config, stats, accounts
    GET  /api/accounts               accounts in memory
    GET  /api/account/{name}         assessment + save plan + brief
    GET  /api/account/{name}/brief   the mental-model brief
    GET  /api/account/{name}/memories  raw memory units for that account
    GET  /api/board                  portfolio triage
    POST /api/ingest                 write a new signal
    POST /api/seed                   load the demo book of business
    POST /api/compare                memory-on vs memory-off ablation
    POST /api/reset                  wipe and re-seed
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import SignalDeskAgent
from .config import Settings
from .memory import MemoryError, _as_dict, items_of
from .seed import build_dataset

log = logging.getLogger("signaldesk.web")
logging.basicConfig(level=logging.WARNING)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# One shared agent keeps memory warm and avoids re-handshaking per request.
# Reflect is slow (10-60s), so assessments are serialized per-process.
_lock = threading.Lock()
_agent: SignalDeskAgent | None = None

# The Hindsight client is sync, but bridges to aiohttp via
# asyncio.get_event_loop(). Called straight from a FastAPI threadpool worker it
# raises "Timeout context manager should be used inside a task", because each
# worker thread ends up with a different loop. Routing every memory call through
# ONE dedicated thread fixes it (that thread has its own consistent loop) and
# serializes access for free, which the slow reflect calls want anyway.
_io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight-io")


def call_blocking(fn, /, *args, **kwargs):
    """Run a blocking Hindsight client call on the dedicated IO thread."""
    return _io.submit(fn, *args, **kwargs).result()


# Account listing is stable between writes, so it is cached briefly. Without
# this, every page load pays a full list_memories round-trip before the UI can
# render anything — the header sits empty for seconds on first paint.
_ACCOUNTS_TTL = 20.0
_cache: dict[str, Any] = {"accounts": None, "at": 0.0, "lock": threading.Lock()}


def cached_accounts(ag: SignalDeskAgent, max_age: float = _ACCOUNTS_TTL) -> list[str]:
    """Account list, cached for `max_age` seconds. Errors are never cached."""
    with _cache["lock"]:
        if _cache["accounts"] is not None and (time.monotonic() - _cache["at"]) < max_age:
            return list(_cache["accounts"])
    names = call_blocking(ag.memory.known_accounts)  # may raise — don't cache failures
    with _cache["lock"]:
        _cache["accounts"] = names
        _cache["at"] = time.monotonic()
    return list(names)


def invalidate_accounts() -> None:
    with _cache["lock"]:
        _cache["accounts"] = None
        _cache["at"] = 0.0


def get_agent() -> SignalDeskAgent:
    global _agent
    if _agent is None:
        try:
            _agent = SignalDeskAgent(Settings.from_env())
        except SystemExit as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _agent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Set the bank up once at boot and release the client on shutdown.

    Uses the modern lifespan API — ``@app.on_event`` is deprecated in FastAPI.
    Setup failures are logged rather than raised so the UI can load and show the
    error instead of a blank connection refusal.
    """
    try:
        call_blocking(lambda: get_agent().setup())
    except Exception:  # noqa: BLE001
        log.exception("startup setup failed")
    yield
    if _agent is not None:
        _agent.close()
    _io.shutdown(wait=False)


app = FastAPI(title="SignalDesk", version="0.1.0", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- models ---------------------------------------------------------------


class IngestIn(BaseModel):
    account: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    channel: str = "note"
    date: str | None = None


class SeedIn(BaseModel):
    reset: bool = False


class CompareIn(BaseModel):
    account: str
    run_control: bool = True


# --- routes ---------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/state")
def state() -> dict[str, Any]:
    ag = get_agent()
    try:
        stats = call_blocking(ag.memory.stats)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        accounts = cached_accounts(ag)
    except Exception as exc:  # noqa: BLE001
        # Say so explicitly — an empty portfolio and a broken one look identical
        # otherwise, and "no risk found" is the dangerous misread.
        log.exception("could not list accounts")
        return {
            "bank": stats.get("bank"),
            "org": ag.settings.org_name,
            "stats": stats,
            "accounts": [],
            "accounts_error": str(exc),
            "directives": [],
            "mental_models": [],
            "memory_ops": ["retain", "reflect", "mental models", "directives"],
        }

    try:
        directive_list = [
            {"name": d.get("name"), "content": d.get("content"), "priority": d.get("priority")}
            for d in call_blocking(
                lambda: items_of(ag.memory._client.list_directives(ag.memory.bank))
            )
        ]
    except Exception:  # noqa: BLE001
        directive_list = []
    try:
        model_list = [
            {"id": m.get("id"), "name": m.get("name"), "tags": m.get("tags")}
            for m in call_blocking(
                lambda: items_of(ag.memory._client.list_mental_models(ag.memory.bank))
            )
        ]
    except Exception:  # noqa: BLE001
        model_list = []
    return {
        "bank": stats.get("bank"),
        "org": ag.settings.org_name,
        "stats": stats,
        "accounts": accounts,
        "directives": directive_list,
        "mental_models": model_list,
        "memory_ops": ["retain", "reflect", "mental models", "directives"],
    }


@app.get("/api/accounts")
def accounts() -> dict[str, Any]:
    try:
        return {"accounts": cached_accounts(get_agent())}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/account/{name}")
def account_detail(name: str) -> dict[str, Any]:
    ag = get_agent()
    try:
        with _lock:
            finding = call_blocking(lambda: ag.analyze(name))
        return finding.to_dict()
    except MemoryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/account/{name}/brief")
def account_brief(name: str) -> dict[str, Any]:
    ag = get_agent()
    try:
        text = call_blocking(lambda: ag.memory.get_brief(name))
        if not text:
            mm_id = call_blocking(lambda: ag.memory.refresh_brief(name))
            text = call_blocking(lambda: ag.memory.get_brief(name))
            return {"account": name, "brief": text, "mental_model_id": mm_id, "created": True}
        return {"account": name, "brief": text, "mental_model_id": None, "created": False}
    except MemoryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/account/{name}/memories")
def account_memories(name: str, limit: int = 40) -> dict[str, Any]:
    """Raw memory units + observations for one account — the 'show your work' view."""
    ag = get_agent()
    try:
        page = call_blocking(
            lambda: _as_dict(ag.memory._client.list_memories(ag.memory.bank, limit=limit))
        )
        items = [m for m in page.get("items", []) if f"acct:{name}" in (m.get("tags") or [])]
        return {"account": name, "total": page.get("total", 0), "items": items}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/board")
def board(limit: int = 6) -> dict[str, Any]:
    ag = get_agent()
    try:
        names = cached_accounts(ag)[:limit]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not read the memory bank: {exc}") from exc
    if not names:
        return {"findings": [], "note": "No accounts in memory yet — seed the demo book first."}
    with _lock:
        findings = call_blocking(lambda: ag.triage(names))
    return {"findings": [f.to_dict() for f in findings], "mode": "triage"}


@app.post("/api/ingest")
def ingest(payload: IngestIn) -> dict[str, Any]:
    from datetime import datetime

    from .llm import read_signal_safely

    ag = get_agent()
    ts = None
    if payload.date:
        try:
            ts = datetime.strptime(payload.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    # A fast read of the raw signal before it is retained. Deliberately NOT used
    # for risk judgment — that stays with reflect over the full history.
    reading, source = read_signal_safely(payload.text)

    channel = payload.channel
    if channel == "note":
        channel = reading.channel  # operator left it on auto

    metadata = {
        "urgency": reading.urgency,
        "sentiment": reading.sentiment,
        "signal_read": reading.summary[:200],
        "read_by": source,
    }
    try:
        res = call_blocking(
            lambda: ag.ingest(
                payload.text,
                payload.account,
                channel=channel,
                when=ts,
                metadata=metadata,
            )
        )
        invalidate_accounts()
        return {"ok": True, "result": res, "reading": reading.to_dict(), "read_by": source}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/seed")
def seed(payload: SeedIn) -> dict[str, Any]:
    ag = get_agent()
    records = build_dataset("demo")
    try:
        if payload.reset:
            call_blocking(ag.memory.reset_bank)
        res = call_blocking(lambda: ag.ingest_history(records))
        invalidate_accounts()
        return {"ok": True, "seeded": res.get("items_count", len(records)), "records": len(records)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/compare")
def compare(payload: CompareIn) -> dict[str, Any]:
    """The ablation: same account, memory-on vs a control bank with no history."""
    from .demo import control_assessment

    ag = get_agent()
    try:
        with _lock:
            full = call_blocking(lambda: ag.analyze(payload.account, refresh_brief=False))
    except MemoryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result: dict[str, Any] = {"account": payload.account, "with_memory": full.to_dict()}
    if payload.run_control:
        result["without_memory"] = control_assessment(payload.account).to_dict()
    return result


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    ag = get_agent()
    call_blocking(ag.memory.reset_bank)
    invalidate_accounts()
    return {"ok": True}


@app.exception_handler(Exception)
def _unhandled(request, exc):  # noqa: ANN001, ANN201
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    # PORT/HOST come from the environment so the same image runs on a laptop and
    # on a platform that injects them (Render, Railway, Fly, Cloud Run).
    uvicorn.run(
        "signaldesk.web:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8420")),
        log_level=os.environ.get("LOG_LEVEL", "warning"),
    )
