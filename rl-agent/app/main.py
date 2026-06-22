import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aio_pika
import asyncpg
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.policy import get_policy, predict_action

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)


class Settings(BaseSettings):
    SERVICE_PORT: int = 8006
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = ""
    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"
    POLICY_MODEL_NAME: str = "rl-trading-policy"

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        if not self.REDIS_URL:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
_pool: asyncpg.Pool | None = None
_tasks: list[asyncio.Task] = []


async def _get_indicators(symbol: str) -> dict:
    if not _pool:
        return {}
    try:
        rows = await _pool.fetch(
            "SELECT close, volume FROM ohlcv WHERE symbol=$1 AND exchange='NSE' AND interval='1d' ORDER BY time DESC LIMIT 30",
            symbol,
        )
        if not rows:
            return {}
        closes = [float(r["close"]) for r in reversed(rows)]
        arr = np.array(closes)
        delta = np.diff(arr)
        gain = np.where(delta > 0, delta, 0).mean()
        loss = np.where(delta < 0, -delta, 0).mean()
        rsi = float(100 - 100 / (1 + gain / (loss + 1e-9)))
        ema12 = arr[-12:].mean() if len(arr) >= 12 else arr.mean()
        ema26 = arr.mean()
        macd_hist = float(ema12 - ema26)
        volumes = [float(r["volume"]) for r in reversed(rows)]
        vol_sma = np.array(volumes).mean()
        vol_ratio = float(volumes[-1]) / (vol_sma + 1e-9)
        return {"rsi_14": rsi, "macd_hist": macd_hist, "volume_ratio": vol_ratio}
    except Exception:
        return {}


def _build_obs(indicators: dict) -> np.ndarray:
    rsi = indicators.get("rsi_14", 50) / 100.0
    macd = indicators.get("macd_hist", 0)
    vol_ratio = indicators.get("volume_ratio", 1.0)
    obs = np.array([rsi, macd, vol_ratio, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    return np.clip(obs, -10, 10)


async def _consumer_loop() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=5)
                queue = await channel.get_queue("market.data.rl")
                signal_exchange = await channel.get_exchange("agent.signals")

                async with queue.iterator() as q_iter:
                    async for message in q_iter:
                        async with message.process():
                            try:
                                body = json.loads(message.body)
                                symbol = body.get("payload", {}).get("symbol") or body.get("symbol")
                                if not symbol:
                                    continue
                                indicators = await _get_indicators(symbol)
                                if not indicators:
                                    logger.warning("RL: no indicators for %s (DB unavailable) — skipping signal", symbol)
                                    continue
                                obs = _build_obs(indicators)
                                policy = get_policy(settings.MLFLOW_TRACKING_URI, settings.POLICY_MODEL_NAME)
                                signal, confidence = predict_action(policy, obs, indicators)

                                msg = {
                                    "event_id": str(uuid.uuid4()),
                                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                                    "service": "rl-agent", "version": "1.0",
                                    "payload": {
                                        "symbol": symbol, "exchange": "NSE", "agent": "rl",
                                        "signal": signal, "confidence": confidence,
                                        "reasoning": f"RL policy {'(MLflow)' if policy else '(momentum fallback)'}",
                                        "indicators": indicators,
                                    },
                                }
                                await signal_exchange.publish(
                                    aio_pika.Message(
                                        body=json.dumps(msg, default=str).encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                        content_type="application/json",
                                    ),
                                    routing_key="rl",
                                )
                                logger.info("RL signal: %s %s (%.2f)", symbol, signal, confidence)
                            except Exception as exc:
                                logger.error("RL message error: %s", exc)
        except Exception as exc:
            logger.error("RL consumer lost: %s — retry 5s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    for attempt in range(1, 11):
        try:
            _pool = await asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=6)
            break
        except Exception:
            await asyncio.sleep(min(2 ** attempt, 30))
    _tasks.append(asyncio.create_task(_consumer_loop(), name="rl-consumer"))
    logger.info("rl-agent ready")
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    if _pool:
        await _pool.close()


app = FastAPI(title="NeuradeX — RL Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    policy = get_policy(settings.MLFLOW_TRACKING_URI, settings.POLICY_MODEL_NAME)
    return {"status": "ok", "service": "rl-agent", "policy_loaded": policy is not None}
