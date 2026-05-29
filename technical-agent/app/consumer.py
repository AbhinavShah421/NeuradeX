"""RabbitMQ consumer: reads market.data.technical queue, publishes to agent.signals."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import aio_pika
import asyncpg

from app.config import settings
from app.signal_generator import generate_signal

logger = logging.getLogger(__name__)


async def _fetch_candles(pool: asyncpg.Pool, symbol: str, interval: str = "1d") -> list[dict]:
    try:
        rows = await pool.fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol=$1 AND exchange='NSE' AND interval=$2
            ORDER BY time DESC LIMIT $3
            """,
            symbol, interval, settings.CANDLE_HISTORY_LIMIT,
        )
        return [dict(r) for r in reversed(rows)]
    except Exception as exc:
        logger.error("Candle fetch failed for %s: %s", symbol, exc)
        return []


async def _build_signal_message(symbol: str, pool: asyncpg.Pool) -> dict:
    candles = await _fetch_candles(pool, symbol, interval="1d")
    if len(candles) < 5:
        candles = await _fetch_candles(pool, symbol, interval="1m")

    result = generate_signal(candles, symbol)
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": settings.SERVICE_NAME,
        "version": "1.0",
        "payload": {
            "symbol": symbol,
            "exchange": "NSE",
            "agent": settings.AGENT_NAME,
            "signal": result["signal"],
            "confidence": result["confidence"],
            "reasoning": result["reasoning"],
            "indicators": result["indicators"],
            "model_votes": result["model_votes"],
        },
    }


async def start_consuming(rabbitmq_url: str, pool: asyncpg.Pool) -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                queue = await channel.get_queue("market.data.technical")
                signal_exchange = await channel.get_exchange("agent.signals")

                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            try:
                                body = json.loads(message.body)
                                symbol = body.get("payload", {}).get("symbol") or body.get("symbol")
                                if not symbol:
                                    continue

                                sig_msg = await _build_signal_message(symbol, pool)

                                await signal_exchange.publish(
                                    aio_pika.Message(
                                        body=json.dumps(sig_msg, default=str).encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                        content_type="application/json",
                                    ),
                                    routing_key="technical",
                                )
                                logger.info(
                                    "Signal: %s %s (%.2f)",
                                    symbol,
                                    sig_msg["payload"]["signal"],
                                    sig_msg["payload"]["confidence"],
                                )
                            except Exception as exc:
                                logger.error("Message processing error: %s", exc)
        except Exception as exc:
            logger.error("Consumer connection lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)
