"""Database helpers for fetching OHLCV training data from TimescaleDB."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import asyncpg

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2)


async def fetch_ohlcv(postgres_url: str, symbol: str, interval: str = "1d", limit: int = 500) -> list[dict]:
    try:
        conn = await asyncpg.connect(postgres_url)
        rows = await conn.fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol=$1 AND exchange='NSE' AND interval=$2
            ORDER BY time ASC
            LIMIT $3
            """,
            symbol, interval, limit,
        )
        await conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("fetch_ohlcv failed for %s: %s", symbol, exc)
        return []


async def fetch_all_symbols(postgres_url: str, symbols: list[str], interval: str = "1d", limit: int = 500) -> dict[str, list[dict]]:
    tasks = {s: fetch_ohlcv(postgres_url, s, interval, limit) for s in symbols}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {sym: (r if isinstance(r, list) else []) for sym, r in zip(tasks.keys(), results)}
