"""Stock Scanner microservice — independently and continuously sweeps the market
for intraday-tradable stocks, maintains the AI watchlist in Redis, runs a fresh
scan before the open, and grades the morning picks after the close to produce a
signal score that feeds the system's learning."""
import asyncio
import logging
from contextlib import asynccontextmanager

import os

from fastapi import Depends, FastAPI, Header, HTTPException

from .scanner import (
    scanner_loop, schedule_loop, scan_once, evaluate_day,
    evaluate_delivery, grade_due_delivery, backfill_delivery, backfill_committed, backfill_intraday,
    get_state, get_latest_eval, warm_state, get_auto_scan, set_auto_scan,
    get_auto_scan_interval, set_auto_scan_interval, next_scan_at,
    agrade_watch_loop, agrade_status, agrade_force_promote,
)
from .universe import UNIVERSE

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger("stock-scanner")

from app.cors import configure_cors

_tasks: list[asyncio.Task] = []
_INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")


def _require_internal(x_api_key: str = Header(None)) -> None:
    """Lightweight internal-API-key guard for mutation endpoints.
    Disabled when INTERNAL_API_KEY is not set (dev mode)."""
    if _INTERNAL_API_KEY and x_api_key != _INTERNAL_API_KEY:
        raise HTTPException(403, "Invalid or missing X-Api-Key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("stock-scanner starting — universe of %d stocks", len(UNIVERSE))
    await warm_state()
    _tasks.append(asyncio.create_task(scanner_loop()))
    _tasks.append(asyncio.create_task(schedule_loop()))
    _tasks.append(asyncio.create_task(agrade_watch_loop()))
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="stock-scanner", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    st = get_state()
    return {"status": "ok", "service": "stock-scanner", **st, "universe": st.get("universe") or len(UNIVERSE)}


@app.get("/auto-scan")
async def auto_scan_status():
    """Auto-scan schedule: enabled flag, gap between sweeps, when the next is due."""
    return {"status": "success", "data": {
        "enabled": await get_auto_scan(),
        "interval": await get_auto_scan_interval(),
        "next_scan_at": next_scan_at(),
    }}


@app.post("/auto-scan")
async def toggle_auto_scan(enabled: bool | None = None, interval: int | None = None,
                           _: None = Depends(_require_internal)):
    """Enable/disable scheduled auto sweeps and/or change the gap between them
    (seconds, clamped 5min–6h). Either parameter may be sent alone."""
    if enabled is not None:
        await set_auto_scan(enabled)
    applied = await set_auto_scan_interval(interval) if interval else await get_auto_scan_interval()
    return {"status": "success", "data": {
        "enabled": await get_auto_scan(), "interval": applied, "next_scan_at": next_scan_at(),
    }}


@app.get("/status")
async def status():
    st = get_state()
    return {"status": "success", "data": {**st, "universe": st.get("universe") or len(UNIVERSE)}}


@app.get("/regime-detail")
async def regime_detail():
    """Full market-regime breakdown — indicators, conditions, and raw values used."""
    st = get_state()
    detail = st.get("regime_detail") or {"regime": st.get("market_regime", "neutral")}
    return {"status": "success", "data": detail}


@app.post("/scan")
async def scan(_: None = Depends(_require_internal)):
    """Trigger an immediate full sweep (manual refresh; also runs continuously)."""
    if get_state().get("running"):
        raise HTTPException(409, "A scan is already running — please wait for it to complete")
    asyncio.create_task(scan_once(phase="manual"))
    return {"status": "started"}


@app.post("/evaluate")
async def evaluate(date: str | None = None, _: None = Depends(_require_internal)):
    """Grade a day's morning watchlist against the actual move (post-market signal score)."""
    asyncio.create_task(evaluate_day(date))
    return {"status": "started"}


@app.get("/evaluation")
async def evaluation():
    """The latest post-market signal-score grade."""
    return {"status": "success", "data": await get_latest_eval()}


@app.post("/evaluate-delivery")
async def evaluate_delivery_ep(date: str | None = None, _: None = Depends(_require_internal)):
    """Grade delivery picks on their multi-day forward return. With a `date` grades
    that entry-date's snapshot; without, grades any whose horizon has elapsed."""
    if date:
        return {"status": "success", "data": await evaluate_delivery(date)}
    asyncio.create_task(grade_due_delivery())
    return {"status": "started"}


@app.post("/backfill-delivery")
async def backfill_delivery_ep(days: int = 14, limit: int = 250, _: None = Depends(_require_internal)):
    """Reconstruct delivery-pick accuracy for the last `days` days so the accuracy
    graph has delivery history immediately. Runs in the background."""
    asyncio.create_task(backfill_delivery(days=days, limit=limit))
    return {"status": "started", "days": days, "limit": limit}


@app.post("/backfill-committed")
async def backfill_committed_ep(days: int = 20, limit: int = 400, _: None = Depends(_require_internal)):
    """Reconstruct the high-conviction tier's accuracy history. Runs in background."""
    asyncio.create_task(backfill_committed(days=days, limit=limit))
    return {"status": "started", "days": days, "limit": limit}


@app.post("/backfill-intraday")
async def backfill_intraday_ep(days: int = 14, limit: int = 400, _: None = Depends(_require_internal)):
    """Reconstruct the intraday signal score so the accuracy graph reaches the
    latest completed day. Runs in background."""
    asyncio.create_task(backfill_intraday(days=days, limit=limit))
    return {"status": "started", "days": days, "limit": limit}


@app.get("/agrade-watch")
async def agrade_watch():
    """Live A-grade watcher snapshot + today's promotions."""
    return {"status": "success", "data": await agrade_status()}


@app.post("/agrade-watch/promote")
async def agrade_promote(symbol: str, force: bool = False, _: None = Depends(_require_internal)):
    """Test hook: promote one symbol through the live-watcher path without
    waiting for its price triggers. force=true also skips the re-score gate."""
    return {"status": "success", "data": await agrade_force_promote(symbol, force=force)}
