import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aio_pika
import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.pattern_detector import generate_pattern_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    SERVICE_PORT: int = 8005
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
    CANDLE_HISTORY_LIMIT: int = 100

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
_pool: asyncpg.Pool | None = None
_tasks: list[asyncio.Task] = []


async def _consumer_loop() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=5)
                queue = await channel.get_queue("market.data.pattern")
                signal_exchange = await channel.get_exchange("agent.signals")

                async with queue.iterator() as q_iter:
                    async for message in q_iter:
                        async with message.process():
                            try:
                                body = json.loads(message.body)
                                symbol = body.get("payload", {}).get("symbol") or body.get("symbol")
                                if not symbol or not _pool:
                                    continue
                                rows = await _pool.fetch(
                                    "SELECT time,open,high,low,close,volume FROM ohlcv WHERE symbol=$1 AND exchange='NSE' AND interval='1d' ORDER BY time DESC LIMIT $2",
                                    symbol, settings.CANDLE_HISTORY_LIMIT,
                                )
                                candles = [dict(r) for r in reversed(rows)]
                                sig = generate_pattern_signal(candles, symbol)
                                msg = {
                                    "event_id": str(uuid.uuid4()),
                                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                                    "service": "pattern-agent", "version": "1.0",
                                    "payload": {
                                        "symbol": symbol, "exchange": "NSE", "agent": "pattern",
                                        "signal": sig["signal"], "confidence": sig["confidence"],
                                        "reasoning": sig["reasoning"],
                                        "indicators": {"patterns": sig["patterns"], "regime": sig["regime"]},
                                    },
                                }
                                await signal_exchange.publish(
                                    aio_pika.Message(
                                        body=json.dumps(msg, default=str).encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                        content_type="application/json",
                                    ),
                                    routing_key="pattern",
                                )
                                logger.info("Pattern signal: %s %s (%.2f) regime=%s", symbol, sig["signal"], sig["confidence"], sig["regime"])
                            except Exception as exc:
                                logger.error("Pattern message error: %s", exc)
        except Exception as exc:
            logger.error("Pattern consumer lost: %s — retry 5s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    for attempt in range(1, 11):
        try:
            _pool = await asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=6)
            break
        except Exception as exc:
            await asyncio.sleep(min(2 ** attempt, 30))
    _tasks.append(asyncio.create_task(_consumer_loop(), name="pattern-consumer"))
    logger.info("pattern-agent ready")
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    if _pool:
        await _pool.close()


app = FastAPI(title="NeuradeX — Pattern Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pattern-agent"}
