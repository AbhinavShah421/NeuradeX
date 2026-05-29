"""Core ingestion loops — runs tick polling, historical backfill, and news fetch."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.sources.groww_source import GrowwSource
from app.sources.yahoo_source import YahooSource
from app.sources.news_source import NewsSource
from app.services.rabbitmq_publisher import RabbitMQPublisher
from app.services.redis_writer import RedisWriter
from app.services.timescale_writer import TimescaleWriter

logger = logging.getLogger(__name__)

INTERVAL_MAP = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 1440: "1d"}


def _is_market_hours() -> bool:
    now = datetime.now(tz=timezone.utc).astimezone()
    # NSE: Mon-Fri 09:15 - 15:30 IST (UTC+5:30)
    from zoneinfo import ZoneInfo
    ist = datetime.now(tz=ZoneInfo("Asia/Kolkata"))
    if ist.weekday() >= 5:
        return False
    market_open = ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= ist <= market_close


async def run_historical_backfill(
    groww: GrowwSource,
    yahoo: YahooSource,
    timescale: TimescaleWriter,
    symbols: list[str],
) -> None:
    logger.info("Starting historical backfill for %d symbols", len(symbols))
    for symbol in symbols:
        try:
            candles = await yahoo.get_daily_history(symbol, days=settings.HISTORICAL_DAYS)
            if not candles:
                logger.warning("No daily history from Yahoo for %s", symbol)
                continue
            for c in candles:
                c["symbol"] = symbol
                c["interval"] = "1d"
                c["source"] = "yahoo"
            count = await timescale.bulk_upsert(candles)
            logger.info("Backfilled %d daily candles for %s", count, symbol)
        except Exception as exc:
            logger.error("Historical backfill failed for %s: %s", symbol, exc)
        await asyncio.sleep(1)   # rate-limit Yahoo requests


async def run_tick_loop(
    groww: GrowwSource,
    yahoo: YahooSource,
    timescale: TimescaleWriter,
    redis: RedisWriter,
    publisher: RabbitMQPublisher,
    symbols: list[str],
) -> None:
    logger.info("Tick loop started for %d symbols", len(symbols))
    while True:
        interval = settings.TICK_INTERVAL_SECONDS if _is_market_hours() else settings.TICK_INTERVAL_SECONDS * 5
        try:
            if groww.is_configured:
                ltp_data = await groww.get_ltp(symbols)
                sample = {k: v for i, (k, v) in enumerate(ltp_data.items()) if i < 2}
                logger.info("Groww LTP: %d symbols, sample=%s", len(ltp_data), sample)
            else:
                ltp_data = {}

            for symbol in symbols:
                try:
                    quote = None

                    if groww.is_configured:
                        raw = ltp_data.get(f"NSE_{symbol}") or ltp_data.get(symbol)
                        price = 0.0
                        raw_dict: dict = {}
                        if isinstance(raw, dict):
                            price = float(raw.get("ltp", 0) or raw.get("last_price", 0) or raw.get("lastPrice", 0) or 0)
                            raw_dict = raw
                        elif isinstance(raw, (int, float)):
                            price = float(raw)
                        if price > 0:
                            now = datetime.now(tz=timezone.utc)
                            quote = {
                                "symbol": symbol,
                                "exchange": "NSE",
                                "ltp": price,
                                "open": float(raw_dict.get("open", price) or price),
                                "high": float(raw_dict.get("high", price) or price),
                                "low": float(raw_dict.get("low", price) or price),
                                "close": price,
                                "volume": int(raw_dict.get("volume", 0) or 0),
                                "timestamp": now.isoformat(),
                                "source": "groww",
                            }

                    if not quote:
                        yq = await yahoo.get_quote(symbol)
                        if isinstance(yq, dict) and float(yq.get("ltp", 0) or 0) > 0:
                            now = datetime.now(tz=timezone.utc)
                            ltp = float(yq["ltp"])
                            quote = {
                                "symbol": symbol,
                                "exchange": "NSE",
                                "ltp": ltp,
                                "open": float(yq.get("open", ltp) or ltp),
                                "high": float(yq.get("high", ltp) or ltp),
                                "low": float(yq.get("low", ltp) or ltp),
                                "close": ltp,
                                "volume": int(yq.get("volume", 0) or 0),
                                "timestamp": now.isoformat(),
                                "source": "yahoo",
                            }

                    if quote:
                        await redis.store_tick(quote)
                        candle = {
                            "time": quote["timestamp"],
                            "symbol": symbol,
                            "exchange": "NSE",
                            "interval": "1m",
                            "open": quote["open"],
                            "high": quote["high"],
                            "low": quote["low"],
                            "close": quote["close"],
                            "volume": quote["volume"],
                            "source": quote["source"],
                        }
                        await timescale.upsert_candle(candle)
                        await publisher.publish_tick(quote)
                        logger.debug("Tick published: %s @ %.2f", symbol, quote["ltp"])

                except Exception as exc:
                    logger.error("Tick processing failed for %s: %s", symbol, exc)

        except Exception as exc:
            logger.error("Tick loop error: %s", exc)

        await asyncio.sleep(interval)


async def run_news_loop(
    news: NewsSource,
    mongodb_url: str,
    publisher: RabbitMQPublisher,
    symbols: list[str],
) -> None:
    if not news.is_configured:
        logger.info("NewsAPI not configured — news loop skipped")
        return

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongodb_url)
    db = client[settings.MONGODB_DB] if hasattr(settings, "MONGODB_DB") else client.stock_prediction
    collection = db.news_articles

    logger.info("News loop started")
    while True:
        try:
            articles = await news.fetch_market_news()
            for sym in symbols[:5]:
                sym_articles = await news.fetch_for_symbol(sym)
                articles.extend(sym_articles)

            if articles:
                docs = []
                for a in articles:
                    doc = dict(a)
                    if isinstance(doc.get("published_at"), datetime):
                        doc["published_at"] = doc["published_at"].isoformat()
                    doc["ingested_at"] = datetime.now(tz=timezone.utc).isoformat()
                    docs.append(doc)

                for doc in docs:
                    await collection.update_one(
                        {"article_id": doc["article_id"]},
                        {"$setOnInsert": doc},
                        upsert=True,
                    )
                await publisher.publish_news_ingested(len(docs))
                logger.info("Ingested %d news articles", len(docs))
        except Exception as exc:
            logger.error("News loop error: %s", exc)

        await asyncio.sleep(settings.NEWS_INTERVAL_SECONDS)
