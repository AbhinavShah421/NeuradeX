"""Stock Scanner microservice — independently and continuously sweeps the market
for intraday-tradable stocks, maintains the AI watchlist in Redis, runs a fresh
scan before the open, and grades the morning picks after the close to produce a
signal score that feeds the system's learning."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .scanner import (
    scanner_loop, schedule_loop, scan_once, evaluate_day,
    evaluate_delivery, grade_due_delivery, backfill_delivery,
    get_state, get_latest_eval, warm_state,
)
from .universe import UNIVERSE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("stock-scanner")

_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("stock-scanner starting — universe of %d stocks", len(UNIVERSE))
    await warm_state()
    _tasks.append(asyncio.create_task(scanner_loop()))
    _tasks.append(asyncio.create_task(schedule_loop()))
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="stock-scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    st = get_state()
    return {"status": "ok", "service": "stock-scanner", **st, "universe": st.get("universe") or len(UNIVERSE)}


@app.get("/status")
async def status():
    st = get_state()
    return {"status": "success", "data": {**st, "universe": st.get("universe") or len(UNIVERSE)}}


@app.post("/scan")
async def scan():
    """Trigger an immediate full sweep (manual refresh; also runs continuously)."""
    asyncio.create_task(scan_once(phase="manual"))
    return {"status": "started"}


@app.post("/evaluate")
async def evaluate(date: str | None = None):
    """Grade a day's morning watchlist against the actual move (post-market signal score)."""
    asyncio.create_task(evaluate_day(date))
    return {"status": "started"}


@app.get("/evaluation")
async def evaluation():
    """The latest post-market signal-score grade."""
    return {"status": "success", "data": await get_latest_eval()}


@app.post("/evaluate-delivery")
async def evaluate_delivery_ep(date: str | None = None):
    """Grade delivery picks on their multi-day forward return. With a `date` grades
    that entry-date's snapshot; without, grades any whose horizon has elapsed."""
    if date:
        return {"status": "success", "data": await evaluate_delivery(date)}
    asyncio.create_task(grade_due_delivery())
    return {"status": "started"}


@app.post("/backfill-delivery")
async def backfill_delivery_ep(days: int = 14, limit: int = 250):
    """Reconstruct delivery-pick accuracy for the last `days` days so the accuracy
    graph has delivery history immediately. Runs in the background."""
    asyncio.create_task(backfill_delivery(days=days, limit=limit))
    return {"status": "started", "days": days, "limit": limit}
