"""Groww live-feed reader (backend side).

The actual websocket streaming lives in the isolated `groww-feed-service`
microservice (it owns the heavy `growwapi` SDK + its pinned deps, which conflict
with the backend's pydantic/pandas/protobuf versions). That service publishes the
latest tick per symbol to Redis as `groww:ltp:{SYMBOL}` = "<price>:<epoch_ts>",
and the set of symbols it should stream as the Redis set `groww:feed:symbols`.

This module is the thin backend reader: paper trading calls `get_ltp()` for a
real-time price and `request_symbols()` to tell the service what to stream.
Everything degrades to 0.0 / no-op if the service or Redis is unavailable, so the
caller falls back to REST/Yahoo.
"""
from __future__ import annotations

import time
from typing import Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_LTP_PREFIX = "groww:ltp:"
_SYMBOLS_SET = "groww:feed:symbols"
_requested: set[str] = set()   # symbols we've already asked the service to stream


async def get_ltp(symbol: str, max_age: float = 20.0) -> float:
    """Latest streamed Groww LTP for `symbol`, or 0.0 if absent/stale.

    Async (reads Redis). Returns the real-time price the feed service published;
    ignores ticks older than `max_age` seconds so a dead feed can't serve stale data.
    """
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_LTP_PREFIX + symbol.upper())
        if not raw:
            return 0.0
        price_s, _, ts_s = raw.partition(":")
        px = float(price_s)
        ts = float(ts_s) if ts_s else 0.0
        if px <= 0 or (time.time() - ts) > max_age:
            return 0.0
        return px
    except Exception:
        return 0.0


async def request_symbols(symbols: list[str]) -> None:
    """Tell the feed service to stream these symbols (adds them to the Redis set).
    Cheap and idempotent — only writes symbols we haven't requested before."""
    new = [s.upper() for s in symbols if s and s.upper() not in _requested]
    if not new:
        return
    try:
        from app.utils.redis_client import get_redis
        await get_redis().sadd(_SYMBOLS_SET, *new)
        _requested.update(new)
    except Exception:
        pass
