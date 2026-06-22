"""Sentiment microservice — pulls Google-News headlines for the AI watchlist,
runs them through the LLM, and caches a news-driven sentiment signal in Redis
for the backend's ensemble to read."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .sentiment import sentiment_loop, refresh_all, get_sentiment, get_state
from . import llm

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger("sentiment-service")

_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sentiment-service starting — provider=%s model=%s", llm.resolve_provider(), llm.active_model())
    _tasks.append(asyncio.create_task(sentiment_loop()))
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)


app = FastAPI(title="sentiment-service", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sentiment-service", **get_state()}


@app.get("/status")
async def status():
    return {"status": "success", "data": {**get_state(),
            "provider": llm.resolve_provider(), "model": llm.active_model()}}


@app.post("/refresh")
async def refresh():
    """Trigger an immediate news-sentiment sweep of the watchlist."""
    asyncio.create_task(refresh_all())
    return {"status": "started"}


@app.get("/sentiment/{symbol}")
async def sentiment(symbol: str):
    return {"status": "success", "data": await get_sentiment(symbol)}
