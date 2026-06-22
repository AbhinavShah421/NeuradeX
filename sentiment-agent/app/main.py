import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.consumer import start_consuming

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    logger.info("sentiment-agent starting — FinBERT model: %s", settings.FINBERT_MODEL)
    _consumer_task = asyncio.create_task(
        start_consuming(settings.RABBITMQ_URL, settings.MONGODB_URL),
        name="sentiment-consumer",
    )
    logger.info("sentiment-agent ready — consuming market.data.sentiment")
    yield
    if _consumer_task:
        _consumer_task.cancel()
        await asyncio.gather(_consumer_task, return_exceptions=True)
    logger.info("sentiment-agent shut down")


app = FastAPI(title="NeuradeX — Sentiment Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "agent": "sentiment"}
