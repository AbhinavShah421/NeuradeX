"""Writes OHLCV candles to PostgreSQL TimescaleDB hypertable."""

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class TimescaleWriter:
    def __init__(self, postgres_url: str):
        # asyncpg expects postgresql:// not postgresql+asyncpg://
        self._url = postgres_url.replace("postgresql+asyncpg://", "postgresql://")
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._url, min_size=2, max_size=10)
        logger.info("TimescaleDB writer connected")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def upsert_candle(self, candle: dict[str, Any]) -> None:
        if not self._pool:
            return
        ts = candle.get("time")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO ohlcv (time, symbol, exchange, interval, open, high, low, close, volume, oi, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (symbol, exchange, interval, time) DO UPDATE SET
                        open   = EXCLUDED.open,
                        high   = EXCLUDED.high,
                        low    = EXCLUDED.low,
                        close  = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        oi     = EXCLUDED.oi,
                        source = EXCLUDED.source
                    """,
                    ts,
                    candle["symbol"],
                    candle.get("exchange", "NSE"),
                    candle.get("interval", "1d"),
                    float(candle["open"]),
                    float(candle["high"]),
                    float(candle["low"]),
                    float(candle["close"]),
                    int(candle["volume"]),
                    candle.get("oi"),
                    candle.get("source", "unknown"),
                )
        except Exception as exc:
            logger.error("TimescaleDB upsert failed for %s: %s", candle.get("symbol"), exc)

    async def bulk_upsert(self, candles: list[dict[str, Any]]) -> int:
        if not self._pool or not candles:
            return 0
        rows = []
        for c in candles:
            ts = c.get("time")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            rows.append((
                ts,
                c["symbol"],
                c.get("exchange", "NSE"),
                c.get("interval", "1d"),
                float(c["open"]),
                float(c["high"]),
                float(c["low"]),
                float(c["close"]),
                int(c["volume"]),
                c.get("oi"),
                c.get("source", "unknown"),
            ))
        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO ohlcv (time, symbol, exchange, interval, open, high, low, close, volume, oi, source)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (symbol, exchange, interval, time) DO NOTHING
                    """,
                    rows,
                )
            return len(rows)
        except Exception as exc:
            logger.error("TimescaleDB bulk_upsert failed: %s", exc)
            return 0

    async def get_recent_candles(
        self, symbol: str, interval: str, limit: int = 200, exchange: str = "NSE"
    ) -> list[dict]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT time, open, high, low, close, volume, oi
                    FROM ohlcv
                    WHERE symbol=$1 AND exchange=$2 AND interval=$3
                    ORDER BY time DESC
                    LIMIT $4
                    """,
                    symbol, exchange, interval, limit,
                )
            return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            logger.error("get_recent_candles failed for %s: %s", symbol, exc)
            return []
