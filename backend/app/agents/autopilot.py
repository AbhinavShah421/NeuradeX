"""Autopilot — the agent that paper-trades the AI Watchlist automatically.

When enabled, during NSE market hours it picks the top BUY signals from the
live watchlist and starts server-side paper-trading sessions for them (capped,
no duplicates). Those sessions are run by the 7-agent ensemble and their
outcomes train the agents — so the whole loop makes the system smarter:

    market scan ─▶ AI watchlist ─▶ autopilot paper-trades it ─▶ outcomes train agents ─▶ better next scan
"""
from __future__ import annotations
import asyncio

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_FLAG_KEY = "ai_engine:autopilot_enabled"
_MAX_CONCURRENT = 3          # how many auto paper sessions at once
_TICK = 90                   # seconds between autopilot checks


async def is_enabled() -> bool:
    try:
        from app.utils.redis_client import cache_get
        return (await cache_get(_FLAG_KEY)) == "1"
    except Exception:
        return False


async def set_enabled(enabled: bool) -> None:
    try:
        from app.utils.redis_client import cache_set
        await cache_set(_FLAG_KEY, "1" if enabled else "0", expire=86400 * 30)
    except Exception as exc:
        logger.warning("autopilot flag save failed: %s", exc)


async def status() -> dict:
    from app.api.paper_trading import _market_status_label
    from app.utils.session_store import list_sessions
    sessions = await list_sessions()
    auto = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"]
    return {
        "enabled": await is_enabled(),
        "market_status": _market_status_label(),
        "running_paper_sessions": len(auto),
        "max_concurrent": _MAX_CONCURRENT,
        "sessions": [{"id": s["id"], "symbol": s["symbol"], "pnl": s.get("metrics", {}).get("total_pnl", 0.0)} for s in auto],
    }


async def _tick() -> None:
    from app.api.paper_trading import _market_status_label
    if _market_status_label() != "open":
        return
    from app.agents.market_scanner import get_watchlist
    from app.utils.session_store import list_sessions
    from app.api.sessions import start_session, StartSessionRequest

    # Trade the top-ranked watchlist picks (BUY signals float to the top of the
    # ranking; if none are BUY, the highest-conviction names are still traded so
    # the system keeps paper-trading and learning). The intraday session then
    # makes its own BUY/SELL/HOLD calls.
    wl = (await get_watchlist()).get("items", [])
    if not wl:
        return

    sessions = await list_sessions()
    running = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"]
    running_syms = {s["symbol"] for s in running}
    slots = _MAX_CONCURRENT - len(running)

    for w in wl:
        if slots <= 0:
            break
        if w["symbol"] in running_syms:
            continue
        try:
            await start_session(StartSessionRequest(mode="paper", symbol=w["symbol"], capital=50_000.0, speed=1))
            slots -= 1
            logger.info("Autopilot started paper session",
                        extra={"log_type": "ai_engine", "event": "autopilot_start", "symbol": w["symbol"]})
        except Exception as exc:
            logger.debug("autopilot could not start %s: %s", w["symbol"], exc)


async def autopilot_loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            if await is_enabled():
                await _tick()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("autopilot loop error: %s", exc)
        await asyncio.sleep(_TICK)
