import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aio_pika
import redis.asyncio as redis_async
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.data_fetcher import fetch_macro_indicators
from app.regime_classifier import generate_macro_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    SERVICE_PORT: int = 8004
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
    MACRO_REFRESH_SECONDS: int = 3600

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
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
_macro_cache: dict = {}
_tasks: list[asyncio.Task] = []


async def _macro_refresh_loop(redis_client: redis_async.Redis) -> None:
    global _macro_cache
    while True:
        try:
            indicators = await fetch_macro_indicators()
            _macro_cache = indicators
            await redis_client.setex("macro:context", 7200, json.dumps(indicators))
            logger.info("Macro indicators refreshed: VIX=%.1f USD/INR=%.2f", indicators["india_vix"], indicators["usd_inr"])
        except Exception as exc:
            logger.error("Macro refresh error: %s", exc)
        await asyncio.sleep(settings.MACRO_REFRESH_SECONDS)


async def _consumer_loop() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=5)
                queue = await channel.get_queue("market.data.macro")
                signal_exchange = await channel.get_exchange("agent.signals")

                async with queue.iterator() as q_iter:
                    async for message in q_iter:
                        async with message.process():
                            try:
                                body = json.loads(message.body)
                                symbol = body.get("payload", {}).get("symbol") or body.get("symbol")
                                if not symbol:
                                    continue
                                if not _macro_cache:
                                    indicators = await fetch_macro_indicators()
                                    _macro_cache.update(indicators)
                                sig = generate_macro_signal(_macro_cache)
                                msg = {
                                    "event_id": str(uuid.uuid4()),
                                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                                    "service": "macro-agent", "version": "1.0",
                                    "payload": {
                                        "symbol": symbol, "exchange": "NSE", "agent": "macro",
                                        "signal": sig["signal"], "confidence": sig["confidence"],
                                        "reasoning": sig["reasoning"],
                                        "indicators": sig["indicators"],
                                        "regime": sig["regime"],
                                    },
                                }
                                await signal_exchange.publish(
                                    aio_pika.Message(
                                        body=json.dumps(msg, default=str).encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                        content_type="application/json",
                                    ),
                                    routing_key="macro",
                                )
                                logger.info("Macro signal: %s %s (%s)", symbol, sig["signal"], sig["regime"])
                            except Exception as exc:
                                logger.error("Macro message error: %s", exc)
        except Exception as exc:
            logger.error("Macro consumer lost: %s — retry 5s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = redis_async.from_url(settings.REDIS_URL, decode_responses=True)
    _tasks.append(asyncio.create_task(_macro_refresh_loop(redis_client), name="macro-refresh"))
    _tasks.append(asyncio.create_task(_consumer_loop(), name="macro-consumer"))
    logger.info("macro-agent ready")
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    await redis_client.aclose()


app = FastAPI(title="NeuradeX — Macro Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "macro-agent", "cached_indicators": bool(_macro_cache)}


@app.get("/macro")
async def get_macro():
    return _macro_cache or {"status": "loading"}
