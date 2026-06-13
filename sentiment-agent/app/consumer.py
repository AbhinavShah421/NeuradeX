"""Consumes market.data.sentiment queue + scores news from MongoDB → publishes signal."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import aio_pika
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.finbert_scorer import score_text, aggregate_scores

logger = logging.getLogger(__name__)


async def _fetch_recent_news(db, symbol: str, window_minutes: int = 60) -> list[dict]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)
    cursor = db.news_articles.find(
        {
            "$or": [{"symbol": symbol}, {"symbol": None}, {"symbol": {"$exists": False}}],
            "published_at": {"$gte": cutoff.isoformat()},
        }
    ).sort("published_at", -1).limit(50)
    return await cursor.to_list(length=50)


def _build_signal(symbol: str, aggregated: dict) -> dict:
    net = aggregated["net_sentiment"]
    count = aggregated["article_count"]

    if count == 0:
        signal, confidence, reason = "HOLD", 0.50, "no recent news"
    elif net > 0.15:
        signal = "BUY"
        confidence = min(0.55 + net * 0.5, 0.85)
        reason = f"bullish sentiment ({net:+.2f}) across {count} articles"
    elif net < -0.15:
        signal = "SELL"
        confidence = min(0.55 + abs(net) * 0.5, 0.85)
        reason = f"bearish sentiment ({net:+.2f}) across {count} articles"
    else:
        signal, confidence, reason = "HOLD", 0.50, f"neutral sentiment ({net:+.2f}) across {count} articles"

    # Dampen confidence when effective weight is low (stale or few articles)
    effective = aggregated.get("effective_articles", float(count))
    if 0 < effective < 3.0:
        confidence = round(confidence * 0.8, 3)

    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": settings.SERVICE_NAME,
        "version": "1.0",
        "payload": {
            "symbol": symbol,
            "exchange": "NSE",
            "agent": "sentiment",
            "signal": signal,
            "confidence": round(confidence, 3),
            "reasoning": reason,
            "indicators": aggregated,
            "model_votes": {"finbert": {"signal": signal, "net_sentiment": net}},
        },
    }


async def start_consuming(rabbitmq_url: str, mongodb_url: str) -> None:
    mongo_client = AsyncIOMotorClient(mongodb_url)
    db = mongo_client.stock_prediction

    while True:
        try:
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=5)
                queue = await channel.get_queue("market.data.sentiment")
                signal_exchange = await channel.get_exchange("agent.signals")

                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            try:
                                body = json.loads(message.body)
                                symbol = body.get("payload", {}).get("symbol") or body.get("symbol")
                                if not symbol:
                                    continue

                                articles = await _fetch_recent_news(
                                    db, symbol, settings.SENTIMENT_WINDOW_MINUTES
                                )

                                scored = []
                                for a in articles:
                                    raw = a.get("raw_text", a.get("title", ""))
                                    if raw:
                                        a["scores"] = score_text(raw, settings.FINBERT_MODEL)
                                        scored.append(a)

                                aggregated = aggregate_scores(scored)
                                sig_msg = _build_signal(symbol, aggregated)

                                await signal_exchange.publish(
                                    aio_pika.Message(
                                        body=json.dumps(sig_msg, default=str).encode(),
                                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                        content_type="application/json",
                                    ),
                                    routing_key="sentiment",
                                )
                                logger.info(
                                    "Sentiment signal: %s %s (%.2f) from %d articles",
                                    symbol,
                                    sig_msg["payload"]["signal"],
                                    sig_msg["payload"]["confidence"],
                                    aggregated["article_count"],
                                )
                            except Exception as exc:
                                logger.error("Sentiment message error: %s", exc)
        except Exception as exc:
            logger.error("Sentiment consumer lost connection: %s — retry in 5s", exc)
            await asyncio.sleep(5)
