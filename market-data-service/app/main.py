"""market-data-service — FastAPI app with background ingestion loops."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.sources.groww_source import GrowwSource
from app.sources.yahoo_source import YahooSource
from app.sources.news_source import NewsSource
from app.services.rabbitmq_setup import setup_topology
from app.services.rabbitmq_publisher import RabbitMQPublisher
from app.services.redis_writer import RedisWriter
from app.services.timescale_writer import TimescaleWriter
from app.services.ingestion_loop import run_tick_loop, run_news_loop, run_historical_backfill

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.cors import configure_cors

_groww = GrowwSource(settings.GROWW_API_KEY, settings.GROWW_API_SECRET)
_yahoo = YahooSource()
_news = NewsSource(settings.NEWSAPI_KEY)
_redis = RedisWriter(settings.REDIS_URL)
_timescale = TimescaleWriter(settings.POSTGRES_URL)
_publisher = RabbitMQPublisher(settings.RABBITMQ_URL)

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("market-data-service starting — watchlist: %s", settings.watchlist_symbols)

    await setup_topology(settings.RABBITMQ_URL)
    await _redis.connect()
    await _timescale.connect()
    await _publisher.connect()

    symbols = settings.watchlist_symbols

    # Historical backfill runs once at startup
    _background_tasks.append(asyncio.create_task(
        run_historical_backfill(_groww, _yahoo, _timescale, symbols),
        name="historical-backfill",
    ))

    # Continuous tick polling
    _background_tasks.append(asyncio.create_task(
        run_tick_loop(_groww, _yahoo, _timescale, _redis, _publisher, symbols),
        name="tick-loop",
    ))

    # News ingestion loop
    _background_tasks.append(asyncio.create_task(
        run_news_loop(_news, settings.MONGODB_URL, _publisher, symbols),
        name="news-loop",
    ))

    logger.info("market-data-service ready")
    yield

    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    await _publisher.close()
    await _redis.close()
    await _timescale.close()
    logger.info("market-data-service shut down")


app = FastAPI(title="NeuradeX — Market Data Service", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "version": "1.0"}


@app.get("/watchlist")
async def get_watchlist():
    return {"symbols": settings.watchlist_symbols}


@app.get("/tick/{symbol}")
async def get_tick(symbol: str):
    tick = await _redis.get_tick(symbol.upper())
    if not tick:
        return JSONResponse(status_code=404, content={"error": f"No tick for {symbol}"})
    return tick


@app.get("/candles/{symbol}")
async def get_candles(symbol: str, interval: str = "1d", limit: int = 200):
    candles = await _timescale.get_recent_candles(symbol.upper(), interval, limit)
    return {"symbol": symbol, "interval": interval, "candles": candles, "count": len(candles)}
