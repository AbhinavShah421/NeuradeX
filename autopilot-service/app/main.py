"""Autopilot microservice — independently runs the paper and backtest training
loops that auto-trade the AI watchlist so the agents keep learning."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .autopilot import paper_loop, backtest_loop, status, set_mode, kick, reset_cursor, _set_batch_size, _set_speed

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger("autopilot")

_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("autopilot-service starting (paper + backtest loops)")
    _tasks.append(asyncio.create_task(paper_loop()))
    _tasks.append(asyncio.create_task(backtest_loop()))
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="autopilot-service", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ControlRequest(BaseModel):
    mode: str = "paper"      # "paper" | "backtest"
    enabled: bool


@app.get("/health")
async def health():
    return {"status": "ok", "service": "autopilot-service"}


@app.get("/status")
async def get_status():
    return {"status": "success", "data": await status()}


@app.post("/control")
async def control(req: ControlRequest):
    mode = req.mode if req.mode in ("paper", "backtest") else "paper"
    try:
        await set_mode(mode, req.enabled)
    except Exception as exc:
        raise HTTPException(503, f"Failed to persist mode change to Redis: {exc}")
    if req.enabled:
        # Start the first queue/tick immediately so the toggle feels instant.
        asyncio.create_task(kick(mode))
    return {"status": "success", "data": await status()}


class BatchSizeRequest(BaseModel):
    batch_size: int


@app.post("/backtest/batch-size")
async def set_batch_size(req: BatchSizeRequest):
    """Change how many backtest sessions run concurrently per batch (1–50)."""
    n = await _set_batch_size(req.batch_size)
    # Kick the queue so a larger batch can fill immediately (no-op if disabled).
    asyncio.create_task(kick("backtest"))
    return {"status": "success", "data": await status()}


class SpeedRequest(BaseModel):
    speed: int


@app.post("/backtest/speed")
async def set_speed(req: SpeedRequest):
    """Change backtest replay speed — candles advanced per step (1–120)."""
    await _set_speed(req.speed)
    return {"status": "success", "data": await status()}


@app.post("/backtest/reset-cursor")
async def reset_backtest_cursor():
    """Reset the backtest 'next trade date' to the last trading day before today."""
    await reset_cursor()
    # If backtest is enabled (and in its allowed window), start the new day's
    # queue right away so the reset feels instant.
    asyncio.create_task(kick("backtest"))
    return {"status": "success", "data": await status()}
