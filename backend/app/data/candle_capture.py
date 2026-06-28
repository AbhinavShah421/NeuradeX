"""Continuous 1-second capture loop — fills the tick-store dataset from the live feed.

Runs in the runner/full role ONLY, so there is a single writer and no cross-container
Parquet races. Each second it samples `groww:ltp:{SYMBOL}` for every streamed symbol,
dedups on the tick's own epoch-second, buffers, and flushes to the tick-store every
FLUSH_SECS. The Groww feed publishes only during market hours, so off-hours the loop
idles cheaply.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.utils.elk_logger import get_logger
from app.data.candle_store import append_ticks, save_minute_volume

logger = get_logger(__name__)

_SYMBOLS_SET   = "groww:feed:symbols"
_ALLOWLIST_KEY = "candle_capture:symbols"   # optional SET — if non-empty, capture ONLY these
_LTP_PREFIX    = "groww:ltp:"
FLUSH_SECS     = 30.0
MAX_AGE        = 20.0        # ignore ticks older than this (dead feed → no stale data)
IST = timezone(timedelta(hours=5, minutes=30))


def _decode_set(raw) -> set[str]:
    out = set()
    for s in (raw or set()):
        out.add(s.decode().upper() if isinstance(s, bytes) else str(s).upper())
    return out


def _market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:                       # Sat / Sun
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 14) <= mins <= (15 * 60 + 31)   # ~09:14–15:31 IST (small margin)


async def _flush(buffer: dict[str, list[tuple[int, float]]]) -> int:
    flushed = 0
    for sym, ticks in list(buffer.items()):
        if ticks:
            flushed += await asyncio.to_thread(append_ticks, sym, ticks)
    buffer.clear()
    return flushed


async def candle_capture_loop() -> None:
    from app.utils.redis_client import get_redis

    buffer:  dict[str, list[tuple[int, float]]] = {}
    last_ts: dict[str, int] = {}
    last_flush = time.time()
    logger.info("candle capture loop started (1s tick store)",
                extra={"log_type": "app_lifecycle", "event": "candle_capture_started"})

    while True:
        try:
            if not _market_hours():
                if buffer:
                    await _flush(buffer)
                    last_flush = time.time()
                await asyncio.sleep(30)
                continue

            r = get_redis()
            syms = _decode_set(await r.smembers(_SYMBOLS_SET))
            allow = _decode_set(await r.smembers(_ALLOWLIST_KEY))
            if allow:                                   # restrict to the allowlisted set
                syms &= allow
            now = time.time()
            for sym in syms:
                raw = await r.get(_LTP_PREFIX + sym)
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode()
                price_s, _, ts_s = raw.partition(":")
                try:
                    price = float(price_s)
                    ts = int(float(ts_s)) if ts_s else 0
                except ValueError:
                    continue
                if price <= 0 or ts <= 0 or (now - ts) > MAX_AGE:
                    continue
                if last_ts.get(sym) == ts:           # no new tick this second
                    continue
                last_ts[sym] = ts
                buffer.setdefault(sym, []).append((ts, price))

            if (now - last_flush) >= FLUSH_SECS and buffer:
                n = await _flush(buffer)
                logger.debug("candle capture flushed %d ticks", n)
                last_flush = now
        except Exception as exc:
            logger.warning("candle capture loop error: %s", exc)

        await asyncio.sleep(1.0)


async def enrich_volume(symbol: str, date_str: str) -> int:
    """Fetch real 1-minute volume from the Groww historical API and store it as
    the per-minute volume sidecar, so resampled bars carry real volume. The live
    LTP stream is price-only, so this is the decoupled volume backfill. Returns
    the number of minutes stored (0 if Groww is unavailable)."""
    from app.utils.groww_client import get_groww_client
    groww = get_groww_client()
    if not groww:
        return 0
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
        raw = await groww.get_historical(symbol, 1, day, day.replace(hour=23, minute=59))
    except Exception as exc:
        logger.debug("enrich_volume %s %s fetch failed: %s", symbol, date_str, exc)
        return 0
    minute_vol: dict[int, int] = {}
    for c in raw or []:
        if isinstance(c, list) and len(c) >= 6:
            minute = (int(c[0]) // 60) * 60
            try:
                minute_vol[minute] = int(float(c[5]))
            except (TypeError, ValueError):
                continue
    if not minute_vol:
        return 0
    return await asyncio.to_thread(save_minute_volume, symbol, date_str, minute_vol)
