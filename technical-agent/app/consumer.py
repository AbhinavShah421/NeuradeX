"""RabbitMQ consumer: reads market.data.technical queue, publishes to agent.signals."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import aio_pika
import asyncpg

from app.config import settings
from app.signal_generator import generate_signal, fuse_timeframe_signals

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
    # Fetch all three timeframes in parallel for multi-timeframe fusion
    candles_1d, candles_1h, candles_15m = await asyncio.gather(
        _fetch_candles(pool, symbol, interval="1d"),
        _fetch_candles(pool, symbol, interval="1h"),
        _fetch_candles(pool, symbol, interval="15m"),
    )

    # Fall back to 1m if higher timeframes are empty (fresh setup)
    if len(candles_1d) < 5 and len(candles_1h) < 5:
        candles_1d = await _fetch_candles(pool, symbol, interval="1m")

    tf_results: dict[str, dict] = {}
    if len(candles_1d) >= 5:
        tf_results["1d"] = generate_signal(candles_1d, symbol)
    if len(candles_1h) >= 5:
        tf_results["1h"] = generate_signal(candles_1h, symbol)
    if len(candles_15m) >= 5:
        tf_results["15m"] = generate_signal(candles_15m, symbol)

    if len(tf_results) > 1:
        fused = fuse_timeframe_signals(tf_results)
        # Use daily indicators for risk-engine (ATR, close price)
        base_indicators = tf_results.get("1d", next(iter(tf_results.values()))).get("indicators", {})
        signal, confidence, reasoning = fused["signal"], fused["confidence"], fused["reasoning"]
        model_votes = {tf: {"signal": r["signal"], "confidence": r["confidence"]} for tf, r in tf_results.items()}
        model_votes["fused"] = {"signal": signal, "confidence": confidence}
    else:
        single = next(iter(tf_results.values())) if tf_results else generate_signal([], symbol)
        signal, confidence, reasoning = single["signal"], single["confidence"], single["reasoning"]
        base_indicators = single.get("indicators", {})
        model_votes = single.get("model_votes", {})

    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": settings.SERVICE_NAME,
        "version": "1.0",
        "payload": {
            "symbol": symbol,
            "exchange": "NSE",
            "agent": settings.AGENT_NAME,
            "signal": signal,
            "confidence": confidence,
            "reasoning": reasoning,
            "indicators": base_indicators,
            "model_votes": model_votes,
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
