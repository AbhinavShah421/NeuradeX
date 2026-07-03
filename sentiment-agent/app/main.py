import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from app.config import settings
from app.consumer import start_consuming
from app.finbert_scorer import score_text, aggregate_scores

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.agent_bootstrap import health_payload
from app.cors import configure_cors

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
configure_cors(app)


class ScoreRequest(BaseModel):
    headlines: list[str]
    published_ats: Optional[list[Optional[str]]] = None


@app.get("/health")
async def health():
    return health_payload(settings.SERVICE_NAME, agent="sentiment")


@app.post("/score")
async def score_headlines(req: ScoreRequest):
    """Score a list of headlines with FinBERT. Returns aggregated net_sentiment and per-label scores.
    Used by the LLM sentiment pipeline for signal blending."""
    articles = []
    pub_ats = req.published_ats or [None] * len(req.headlines)
    for headline, pub_at in zip(req.headlines, pub_ats):
        try:
            sc = score_text(headline, settings.FINBERT_MODEL)
        except Exception:
            sc = {"positive": 0.33, "negative": 0.33, "neutral": 0.34}
        articles.append({"raw_text": headline, "source": "headline",
                         "published_at": pub_at, "scores": sc})
    agg = aggregate_scores(articles)
    return {"status": "ok", "data": agg}
