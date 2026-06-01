"""Autopilot — the agent that paper-trades the AI Watchlist automatically.

When enabled, the autopilot runs by itself during NSE market hours: every tick it
reads the live AI watchlist (produced by the independent stock-scanner) and opens
a server-side paper-trading session for **every** watchlist stock it hasn't
already traded today. Each session runs on real market data and is driven by the
full 7-agent ensemble (+ pattern memory), and every closed trade trains the
agents — so paper trading is the engine that keeps making the system smarter:

    market scan ─▶ AI watchlist ─▶ autopilot paper-trades it all ─▶ outcomes train agents ─▶ better next scan

Rules:
  • Only runs while autopilot mode is ON and the market is open.
  • One session per symbol per trading day (no churn; resets each day).
  • Sessions are the normal paper sessions, so they show up on the Paper Trading
    page and can be opened as a live chart like any other.
"""
from __future__ import annotations
import asyncio
import json
import os

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_FLAG_KEY = "ai_engine:autopilot_enabled"

# How many paper sessions autopilot may run at once. Defaults high enough to
# cover the whole watchlist (scanner TOP_N=15) so it trades *all* the picks.
_MAX_CONCURRENT = int(os.getenv("AUTOPILOT_MAX_SESSIONS", "15"))
_CAPITAL        = float(os.getenv("AUTOPILOT_CAPITAL", "50000"))
_TICK           = int(os.getenv("AUTOPILOT_TICK_SECS", "60"))   # seconds between checks


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


# ── Per-day "already traded" tracking (one session per symbol per day) ────────

def _started_key(date_str: str) -> str:
    return f"ai_engine:autopilot:started:{date_str}"


async def _started_today(date_str: str) -> set[str]:
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_started_key(date_str))
        if raw:
            return set(json.loads(raw))
    except Exception:
        pass
    return set()


async def _mark_started(date_str: str, symbol: str) -> None:
    try:
        from app.utils.redis_client import cache_set
        syms = await _started_today(date_str)
        syms.add(symbol)
        await cache_set(_started_key(date_str), json.dumps(sorted(syms)), expire=86400 * 2)
    except Exception as exc:
        logger.debug("autopilot mark_started skipped: %s", exc)


# ── Status ────────────────────────────────────────────────────────────────────

async def status() -> dict:
    from app.api.paper_trading import _market_status_label, _now_ist
    from app.utils.session_store import list_sessions
    from app.agents.market_scanner import get_watchlist

    date_str = _now_ist().strftime("%Y-%m-%d")
    sessions = await list_sessions()
    auto = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"]
    wl = (await get_watchlist()).get("items", [])
    started = await _started_today(date_str)
    return {
        "enabled": await is_enabled(),
        "market_status": _market_status_label(),
        "date": date_str,
        "watchlist_size": len(wl),
        "started_today": len(started),
        "running_paper_sessions": len(auto),
        "max_concurrent": _MAX_CONCURRENT,
        "capital_per_session": _CAPITAL,
        "sessions": [
            {"id": s["id"], "symbol": s["symbol"],
             "pnl": s.get("metrics", {}).get("total_pnl", 0.0)}
            for s in auto
        ],
    }


# ── Tick ──────────────────────────────────────────────────────────────────────

async def _tick() -> None:
    from app.api.paper_trading import _market_status_label, _now_ist
    if _market_status_label() != "open":
        return
    from app.agents.market_scanner import get_watchlist
    from app.utils.session_store import list_sessions
    from app.api.sessions import start_session, StartSessionRequest

    date_str = _now_ist().strftime("%Y-%m-%d")
    wl = (await get_watchlist()).get("items", [])
    if not wl:
        return

    sessions = await list_sessions()
    running = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"]
    running_syms = {s["symbol"] for s in running}
    started = await _started_today(date_str)        # symbols autopilot already opened today
    slots = _MAX_CONCURRENT - len(running)

    # Open a paper session for every watchlist stock not yet traded today, up to
    # the concurrency cap. Watchlist is already ranked (BUY/conviction first), so
    # the most promising names are picked first if the cap binds.
    for w in wl:
        if slots <= 0:
            break
        sym = w["symbol"]
        if sym in running_syms or sym in started:
            continue
        try:
            await start_session(StartSessionRequest(mode="paper", symbol=sym, capital=_CAPITAL, speed=1))
            await _mark_started(date_str, sym)
            slots -= 1
            logger.info("Autopilot started paper session",
                        extra={"log_type": "ai_engine", "event": "autopilot_start",
                               "symbol": sym, "date": date_str})
        except Exception as exc:
            # Market just opened / no candle yet / transient — retry next tick.
            logger.debug("autopilot could not start %s: %s", sym, exc)


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
