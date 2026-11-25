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
async def audit(full: bool = False):
    """Verify the output of every component on the decision path (read-only).
    `full=true` adds the slow end-to-end items (infra sweep, labels) and is what
    the watcher's checklist round uses."""
    from app.services.component_audit import run_audit
    return {"status": "success", "data": await run_audit(full=full)}


@router.get("/losses")
async def losses(hours: int = 3):
    """Deep-dive of every losing trade closed in the window: per-trade post-mortem
    (price path, entry-vs-exit blame, setup, votes) plus causes vs a winners control."""
    from app.services.component_audit import loss_review
    return {"status": "success", "data": await loss_review(max(1, min(hours, 72)))}


@router.get("/rallies")
async def rallies():
    """Paper stocks up >= 1.5% (from open or day low) with no position, and why the gate holds off."""
    from app.services.component_audit import live_rallies
    return {"status": "success", "data": await live_rallies()}


@router.get("/open-movers")
async def open_movers(date: str, n: int = 15):
    """The opening gainers board for a past trading day (open / previous close,
    no lookahead): what the backtest autopilot replays so research trades the
    same kind of stock paper does."""
    from app.research.open_movers import open_movers as _om
    return {"status": "success", "data": await _om(date, max(1, min(n, 50)))}


@router.get("/missed")
async def missed(hours: int = 3):
    """No-trade sessions whose price rose >= 1.5%: the gate that blocked the start
    of the rise, and the entry's counterfactual P&L under the live exits."""
    from app.services.component_audit import missed_uptrends
    return {"status": "success", "data": await missed_uptrends(max(1, min(hours, 72)))}


@router.get("/activity")
async def activity(minutes: int = 15):
    """Per-mode entries/exits/vetoes and trade-record sanity over the last N minutes."""
    from app.services.component_audit import activity as _activity
    return {"status": "success", "data": await _activity(min(max(minutes, 1), 720))}


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
