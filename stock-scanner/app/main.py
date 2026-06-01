"""Stock Scanner microservice — independently and continuously sweeps the market
for intraday-tradable stocks and maintains the AI watchlist in Redis."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .scanner import scanner_loop, scan_once, get_state
from .universe import UNIVERSE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("stock-scanner")

_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("stock-scanner starting — universe of %d stocks", len(UNIVERSE))
    _tasks.append(asyncio.create_task(scanner_loop()))
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="stock-scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "stock-scanner", **get_state(), "universe": len(UNIVERSE)}


@app.get("/status")
async def status():
    return {"status": "success", "data": {**get_state(), "universe": len(UNIVERSE)}}


@app.post("/scan")
async def scan():
    """Trigger an immediate full sweep (also runs continuously in the background)."""
    asyncio.create_task(scan_once())
    return {"status": "started"}
