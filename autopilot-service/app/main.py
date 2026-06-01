"""Autopilot microservice — independently runs the paper and backtest training
loops that auto-trade the AI watchlist so the agents keep learning."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .autopilot import paper_loop, backtest_loop, status, set_mode, kick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("autopilot")

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
    await set_mode(mode, req.enabled)
    if req.enabled:
        # Start the first queue/tick immediately so the toggle feels instant.
        asyncio.create_task(kick(mode))
    return {"status": "success", "data": await status()}
