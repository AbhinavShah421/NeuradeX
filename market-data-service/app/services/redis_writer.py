"""Caches latest tick for each symbol in Redis with TTL."""

import json
import logging
from typing import Any

import redis.asyncio as redis_async

logger = logging.getLogger(__name__)

TICK_TTL = 120   # seconds


class RedisWriter:
    def __init__(self, redis_url: str):
        self._url = redis_url
        self._client: redis_async.Redis | None = None

    async def connect(self) -> None:
        self._client = redis_async.from_url(self._url, decode_responses=True)
        await self._client.ping()
        logger.info("Redis writer connected")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def store_tick(self, tick: dict[str, Any]) -> None:
        if not self._client:
            return
        symbol = tick.get("symbol", "UNKNOWN")
        key = f"tick:{symbol}"
        try:
            await self._client.setex(key, TICK_TTL, json.dumps(tick, default=str))
        except Exception as exc:
            logger.error("Redis store_tick failed for %s: %s", symbol, exc)

    async def get_tick(self, symbol: str) -> dict | None:
        if not self._client:
            return None
        try:
            raw = await self._client.get(f"tick:{symbol}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def store_macro_context(self, context: dict[str, Any]) -> None:
        if not self._client:
            return
        try:
            await self._client.setex("macro:context", 7200, json.dumps(context, default=str))
        except Exception as exc:
            logger.error("Redis store_macro failed: %s", exc)

    async def get_macro_context(self) -> dict | None:
        if not self._client:
            return None
        try:
            raw = await self._client.get("macro:context")
            return json.loads(raw) if raw else None
        except Exception:
            return None
