"""Supervisor API — read state, and (authenticated) override paper trading.

Thin router over app.services.supervisor. Reads are open like the rest of the
session API; every mutation requires a logged-in user and is written to the
supervisor log with the acting identity.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services import supervisor as sv

router = APIRouter()


class ReasonBody(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class SymbolBody(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=30)
    reason: str = Field(..., min_length=3, max_length=500)


class ForceExitBody(BaseModel):
    session_id: str = Field(..., min_length=4, max_length=64)
    reason: str = Field(..., min_length=3, max_length=500)


def _who(user: dict) -> str:
    return str(user.get("email") or user.get("sub") or "unknown")


@router.get("/state")
async def state():
    return {"status": "success", "data": await sv.get_state()}


@router.get("/audit")
async def audit():
    """Verify the output of every component on the paper decision path (read-only)."""
    from app.services.component_audit import run_audit
    return {"status": "success", "data": await run_audit()}


class AskBody(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    context: Optional[dict] = None


@router.post("/ask")
async def ask(body: AskBody, user: dict = Depends(get_current_user)):
    """Queue a question for the supervising trader (answered at /answers)."""
    qid = await sv.ask(body.question, _who(user), body.context)
    return {"status": "success", "data": {"id": qid}}


@router.get("/inbox")
async def inbox(limit: int = 50):
    return {"status": "success", "data": await sv.get_inbox(min(max(limit, 1), 500))}


@router.get("/answers")
async def answers(limit: int = 50):
    return {"status": "success", "data": await sv.get_answers(min(max(limit, 1), 500))}


@router.get("/log")
async def log(limit: int = 100):
    return {"status": "success", "data": await sv.get_log(min(max(limit, 1), 2000))}


@router.post("/config")
async def config(patch: dict, user: dict = Depends(get_current_user)):
    try:
        cfg = await sv.update_config(patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await sv.log_event("config_changed", by=_who(user), patch=patch)
    return {"status": "success", "data": cfg}


@router.post("/halt")
async def halt(body: ReasonBody, user: dict = Depends(get_current_user)):
    await sv.set_halt(body.reason)
    await sv.log_event("halt", by=_who(user), reason=body.reason)
    return {"status": "success"}


@router.delete("/halt")
async def resume(user: dict = Depends(get_current_user)):
    await sv.set_halt(None)
    await sv.log_event("resume", by=_who(user))
    return {"status": "success"}


@router.post("/veto")
async def veto(body: SymbolBody, user: dict = Depends(get_current_user)):
    await sv.veto_symbol(body.symbol, body.reason)
    await sv.log_event("veto_symbol", by=_who(user), symbol=body.symbol.upper(), reason=body.reason)
    return {"status": "success"}


@router.delete("/veto/{symbol}")
async def unveto(symbol: str, user: dict = Depends(get_current_user)):
    await sv.unveto_symbol(symbol)
    await sv.log_event("unveto_symbol", by=_who(user), symbol=symbol.upper())
    return {"status": "success"}


@router.post("/approve")
async def approve(body: SymbolBody, user: dict = Depends(get_current_user)):
    await sv.approve_symbol(body.symbol, body.reason)
    await sv.log_event("approve_symbol", by=_who(user), symbol=body.symbol.upper(), reason=body.reason)
    return {"status": "success"}


@router.delete("/approve/{symbol}")
async def unapprove(symbol: str, user: dict = Depends(get_current_user)):
    await sv.unapprove_symbol(symbol)
    await sv.log_event("unapprove_symbol", by=_who(user), symbol=symbol.upper())
    return {"status": "success"}


@router.post("/force-exit")
async def force_exit(body: ForceExitBody, user: dict = Depends(get_current_user)):
    from app.utils.session_store import get_session
    s = await get_session(body.session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    if s.get("mode") != "paper":
        raise HTTPException(400, "Supervisor force-exit is paper-only.")
    if (s.get("position") or {}).get("status") != "LONG":
        raise HTTPException(409, "Session has no open position.")
    await sv.request_force_exit(body.session_id, body.reason)
    await sv.log_event("force_exit_requested", by=_who(user), session_id=body.session_id,
                       symbol=s.get("symbol"), reason=body.reason)
    return {"status": "success", "note": "exits on the session's next decided candle (~1 min)"}
