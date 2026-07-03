"""Live Trading Sessions — server-side, background-advancing trade sessions.

A session (AI Live Trading *replay* of a past day, or live *paper* trading) runs
on the server, not in the browser. State lives in Redis, a background loop
advances every running session candle-by-candle using the full 7-agent ensemble
(+ pattern memory), and the frontend simply reads state. This is what makes a
session survive a refresh, keep running in the background, run alongside others,
and be reopenable as a live chart.

This is a thin FastAPI router: it parses/validates requests and delegates all
business logic (state machine, order execution, background advancement loop)
to app.services.sessions_service. See that module's docstring for the list of
names re-exported below for other modules that import internals from here.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends

from app.api.auth import get_current_user
from app.utils.elk_logger import get_logger
from app.services import sessions_service as service

# Pydantic request models — defined in the service module, imported here for
# use as route parameter types. Schema is unchanged, only the module moved.
from app.services.sessions_service import StartSessionRequest, SpeedRequest, PaperConfigRequest

# Re-exported for external consumers that import these names directly from
# app.api.sessions — do not remove without updating those call sites:
#   app.main: session_runner_loop
#   app.services.ai_engine_service: get_trade_gate, TRADE_GATES,
#     TRADE_GATE_KEY, _gate_cache
from app.services.sessions_service import (  # noqa: F401
    session_runner_loop,
    get_trade_gate,
    TRADE_GATES,
    TRADE_GATE_KEY,
    _gate_cache,
)

logger = get_logger(__name__)
router = APIRouter()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_session(req: StartSessionRequest):
    return await service.start_session(req)


@router.get("/statuses")
async def session_statuses():
    """Lightweight endpoint: returns {id → status} for all sessions.

    Used by the autopilot to monitor queue completion without loading full
    session summaries (~150 KB → ~500 bytes per response).
    Uses a Redis pipeline so all GETs are a single round trip.
    """
    return await service.session_statuses()


@router.get("")
@router.get("/")
async def list_all_sessions(limit: int = 50, status: str | None = None):
    """Returns the most recent `limit` sessions (default 50, max 200).
    Optional ?status=running filter returns only sessions with that status.
    Uses slim Redis blobs — no candle arrays loaded."""
    return await service.list_all_sessions(limit, status)


# ── Paper trading time config (must be before /{session_id} to avoid shadowing)

@router.get("/paper-config")
async def get_paper_trading_config():
    return await service.get_paper_trading_config()


@router.post("/paper-config")
async def set_paper_trading_config(req: PaperConfigRequest, user: dict = Depends(get_current_user)):
    return await service.set_paper_trading_config(req)


@router.get("/{session_id}")
async def get_one_session(session_id: str):
    return await service.get_one_session(session_id)


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, user: dict = Depends(get_current_user)):
    return await service.stop_session(session_id)


@router.post("/{session_id}/speed")
async def set_speed(session_id: str, req: SpeedRequest, user: dict = Depends(get_current_user)):
    return await service.set_speed(session_id, req)


@router.delete("/{session_id}")
async def remove_session(session_id: str, user: dict = Depends(get_current_user)):
    return await service.remove_session(session_id)
