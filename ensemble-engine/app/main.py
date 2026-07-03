import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aio_pika
import redis.asyncio as redis_async
import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.aggregator import aggregate_signals, DEFAULT_WEIGHTS
from app.agent_collector import AgentSignalCollector
from app.meta_model import predict_win_probability, load_meta_model
from app.calibrator import calibrate_confidence, load_calibrator

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.agent_bootstrap import connect_with_retry, health_payload
from app.cors import configure_cors


class Settings(BaseSettings):
    SERVICE_PORT: int = 8007
    SERVICE_NAME: str = "ensemble-engine"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = ""
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""
    AGENT_SIGNAL_TIMEOUT_SECONDS: float = 5.0
    MIN_CONFIDENCE_TO_TRADE: float = 0.60
    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"
    META_WIN_PROB_GATE: float = 0.52

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        if not self.REDIS_URL:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

_INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")


def _require_internal(x_api_key: str = Header(None)) -> None:
    if _INTERNAL_API_KEY and x_api_key != _INTERNAL_API_KEY:
        raise HTTPException(403, "Invalid or missing X-Api-Key")


_pool: asyncpg.Pool | None = None
_redis: redis_async.Redis | None = None
_publisher: aio_pika.RobustConnection | None = None
_tasks: list[asyncio.Task] = []
_recent_decisions: list[dict] = []


async def _load_weights_from_db() -> dict[str, float]:
    if not _pool:
        return dict(DEFAULT_WEIGHTS)
    try:
        rows = await _pool.fetch("SELECT agent, weight FROM agent_weights")
        if rows:
            return {r["agent"]: float(r["weight"]) for r in rows}
    except Exception as exc:
        logger.warning("Could not load weights from DB: %s — using defaults", exc)
    return dict(DEFAULT_WEIGHTS)


async def on_all_signals_received(symbol: str, agent_signals: dict) -> None:
    weights = await _load_weights_from_db()

    # Extract macro regime from the macro-agent signal so ensemble can
    # shift weights toward agents that perform best in this environment.
    regime = "NEUTRAL"
    macro_sig = agent_signals.get("macro", {})
    if isinstance(macro_sig, dict):
        regime = macro_sig.get("regime", "NEUTRAL")

    result = aggregate_signals(agent_signals, weights, regime=regime)

    # Secondary gate: meta-model WIN probability (trained on historical trades)
    meta_win_prob = predict_win_probability(
        result["agent_votes"],
        result["weighted_confidence"],
        settings.MLFLOW_TRACKING_URI,
    )

    # Primary confidence gate
    if result["weighted_confidence"] < settings.MIN_CONFIDENCE_TO_TRADE:
        result["final_action"] = "HOLD"

    # Meta-model gate: if meta-model is loaded and predicts low WIN probability, hold
    if meta_win_prob is not None and meta_win_prob < settings.META_WIN_PROB_GATE:
        logger.info(
            "Meta-model gate: WIN_PROB=%.3f < %.3f — downgrading %s to HOLD",
            meta_win_prob, settings.META_WIN_PROB_GATE, result["final_action"],
        )
        result["final_action"] = "HOLD"

    # Calibrate the confidence score so it reflects true WIN probability
    raw_confidence = result["weighted_confidence"]
    calibrated_confidence = calibrate_confidence(raw_confidence, settings.MLFLOW_TRACKING_URI)
    result["weighted_confidence"] = round(calibrated_confidence, 3)
    result["raw_confidence"] = round(raw_confidence, 3)

    # Extract price and ATR from technical agent indicators (used by risk-engine)
    current_price = 0.0
    atr = 0.0
    for agent_data in agent_signals.values():
        indicators = agent_data.get("indicators", {})
        if isinstance(indicators, dict):
            if not current_price and indicators.get("close"):
                current_price = float(indicators["close"])
            if not atr and indicators.get("atr"):
                atr = float(indicators["atr"])
            if current_price and atr:
                break

    decision = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": settings.SERVICE_NAME,
        "version": "1.0",
        "payload": {
            "symbol": symbol,
            "exchange": "NSE",
            "final_action": result["final_action"],
            "weighted_confidence": result["weighted_confidence"],
            "agent_votes": result["agent_votes"],
            "agreement_score": result["agreement_score"],
            "uncertainty": result["uncertainty"],
            "scores": result["scores"],
            "weights_used": weights,
            "regime": regime,
            "meta_win_probability": round(meta_win_prob, 3) if meta_win_prob is not None else None,
            "raw_confidence": result.get("raw_confidence"),
            "current_price": current_price,
            "atr": atr,
        },
    }

    logger.info(
        "ENSEMBLE DECISION: %s → %s (conf=%.2f, agreement=%.2f)",
        symbol,
        result["final_action"],
        result["weighted_confidence"],
        result["agreement_score"],
    )

    # Store in Redis for API access
    if _redis:
        try:
            await _redis.setex(
                f"ensemble:{symbol}",
                300,
                json.dumps(decision, default=str),
            )
        except Exception as exc:
            logger.error("Redis decision store failed: %s", exc)

    # Publish to ensemble.decision exchange
    # Always acquire a fresh channel from the robust connection so a RabbitMQ restart
    # doesn't leave us with a permanently stale _pub_channel.
    if _publisher:
        try:
            ch = await _publisher.channel()
            exchange = await ch.get_exchange("ensemble.decision")
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(decision, default=str).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="decision",
            )
            await ch.close()
        except Exception as exc:
            logger.error("Ensemble publish failed: %s", exc)

    _recent_decisions.insert(0, decision)
    if len(_recent_decisions) > 50:
        _recent_decisions.pop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _redis, _publisher

    _pool = await connect_with_retry(
        lambda: asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=6),
        what="ensemble-engine postgres",
        required=False,
    )

    _redis = redis_async.from_url(settings.REDIS_URL, decode_responses=True)

    _publisher = await connect_with_retry(
        lambda: aio_pika.connect_robust(settings.RABBITMQ_URL),
        what="ensemble-engine rabbitmq",
        required=True,
    )

    collector = AgentSignalCollector(timeout_seconds=settings.AGENT_SIGNAL_TIMEOUT_SECONDS)
    collector.on_decision_ready(on_all_signals_received)

    _tasks.append(asyncio.create_task(collector.start(settings.RABBITMQ_URL), name="ensemble-collector"))

    # Pre-load meta-model and calibrator so first inference doesn't block
    load_meta_model(settings.MLFLOW_TRACKING_URI)
    load_calibrator(settings.MLFLOW_TRACKING_URI)

    logger.info("ensemble-engine ready — timeout=%.1fs min_confidence=%.2f meta_gate=%.2f",
                settings.AGENT_SIGNAL_TIMEOUT_SECONDS, settings.MIN_CONFIDENCE_TO_TRADE,
                settings.META_WIN_PROB_GATE)
    yield

    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    if _publisher:
        await _publisher.close()
    if _redis:
        await _redis.aclose()
    if _pool:
        await _pool.close()


app = FastAPI(title="NeuradeX — Ensemble Engine", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    return health_payload(settings.SERVICE_NAME, db_pool=_pool is not None)


@app.get("/decision/{symbol}")
async def get_decision(symbol: str):
    if not _redis:
        return {"error": "not ready"}
    raw = await _redis.get(f"ensemble:{symbol.upper()}")
    if not raw:
        return {"error": f"no recent decision for {symbol}"}
    return json.loads(raw)


@app.get("/decisions/recent")
async def get_recent_decisions(limit: int = 20, _: None = Depends(_require_internal)):
    return {"decisions": _recent_decisions[:limit]}


@app.get("/weights")
async def get_weights():
    weights = await _load_weights_from_db()
    return {"weights": weights}
