"""Continuous 1-second capture loop — fills the tick-store dataset from the live feed.

Runs in the runner/full role ONLY, so there is a single writer and no cross-container
Parquet races. Each second it samples `groww:ltp:{SYMBOL}` for every streamed symbol,
dedups on the tick's own epoch-second, buffers, and flushes to the tick-store every
FLUSH_SECS.

Fallback chain (the dataset must survive a Groww feed outage):
  1. Groww live stream ticks (primary — ~1s resolution).
  2. Yahoo real-time price (chart meta.regularMarketPrice) for any symbol whose
     Groww tick is stale/missing — polled at most every _YF_MIN_INTERVAL per symbol,
     so an outage degrades to ~10s resolution instead of a hole in the day.

Off-hours the loop idles cheaply and runs the automatic volume enrichment: the
live streams are price-only, so after each close it backfills real 1-minute volume
(Groww historical first, Yahoo 1-min chart as fallback) for every symbol captured
that day. Real volume is what makes VWAP / volume-ratio indicators meaningful when
backtests and replays train the agents on this dataset.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.utils.elk_logger import get_logger
from app.data.candle_store import (
    append_ticks, save_minute_volume, symbols_with_ticks, has_minute_volume,
    day_coverage,
)

logger = get_logger(__name__)

_SYMBOLS_SET   = "groww:feed:symbols"
_ALLOWLIST_KEY = "candle_capture:symbols"   # optional SET — if non-empty, capture ONLY these
_LTP_PREFIX    = "groww:ltp:"
FLUSH_SECS     = 30.0
MAX_AGE        = 20.0        # ignore ticks older than this (dead feed → no stale data)
IST = timezone(timedelta(hours=5, minutes=30))

# ── Yahoo live fallback (capture continuity during Groww feed outages) ────────
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
_YF_STALE_AFTER   = 15.0    # no Groww tick fresher than this → symbol needs fallback
_YF_MIN_INTERVAL  = 10.0    # per-symbol floor between Yahoo fallback fetches
_YF_TICK_MAX_AGE  = 180.0   # discard Yahoo prices older than this (their own trade time)
_YF_CONCURRENCY   = 8
_YF_MAX_PER_CYCLE = 40      # cap fallback fetches per loop iteration

_yf_last: dict[str, float] = {}   # symbol → last fallback attempt (epoch)

# ── Post-close volume enrichment ──────────────────────────────────────────────
_ENRICH_EVERY    = 600.0    # seconds between enrichment sweeps while off-hours
_ENRICH_BATCH    = 25       # symbols per sweep (catches up across sweeps)
_ENRICH_MAX_TRIES = 12      # give up on a symbol/day after this many failed sweeps
_last_enrich = 0.0


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


async def _yahoo_live_tick(client, symbol: str) -> tuple[str, int, float]:
    """(symbol, trade_epoch, price) from Yahoo's chart meta — (sym, 0, 0.0) on miss.
    interval=1d keeps the payload tiny; meta.regularMarketPrice is the live price."""
    try:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
            params={"range": "1d", "interval": "1d", "includePrePost": "false"},
            headers=_YF_HEADERS,
        )
        r.raise_for_status()
        meta = ((r.json().get("chart", {}).get("result") or [{}])[0] or {}).get("meta") or {}
        px = float(meta.get("regularMarketPrice") or 0)
        ts = int(meta.get("regularMarketTime") or 0)
        if px > 0 and ts > 0:
            return symbol, ts, px
    except Exception:
        pass
    return symbol, 0, 0.0


async def _yahoo_fallback_fill(symbols: list[str],
                               buffer: dict[str, list[tuple[int, float]]],
                               last_ts: dict[str, int]) -> int:
    """Fetch Yahoo live prices for symbols the Groww feed isn't covering right now
    and append them as ticks (dedup on Yahoo's own trade timestamp)."""
    import httpx
    now = time.time()
    for s in symbols:
        _yf_last[s] = now                     # rate-limit even failed attempts
    added = 0
    sem = asyncio.Semaphore(_YF_CONCURRENCY)

    async def one(client, sym: str):
        async with sem:
            return await _yahoo_live_tick(client, sym)

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            results = await asyncio.gather(*(one(client, s) for s in symbols),
                                           return_exceptions=True)
    except Exception:
        return 0
    for res in results:
        if not isinstance(res, tuple):
            continue
        sym, ts, px = res
        if px <= 0 or ts <= 0 or (now - ts) > _YF_TICK_MAX_AGE:
            continue
        if last_ts.get(sym) == ts:            # no newer trade than what we have
            continue
        last_ts[sym] = ts
        buffer.setdefault(sym, []).append((ts, px))
        added += 1
    return added


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
                await _auto_enrich_volume()          # throttled internally
                await asyncio.sleep(30)
                continue

            r = get_redis()
            syms = _decode_set(await r.smembers(_SYMBOLS_SET))
            allow = _decode_set(await r.smembers(_ALLOWLIST_KEY))
            if allow:                                   # restrict to the allowlisted set
                syms &= allow
            now = time.time()
            fresh: set[str] = set()
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
                if (now - ts) <= _YF_STALE_AFTER:
                    fresh.add(sym)               # Groww is covering this symbol
                if last_ts.get(sym) == ts:           # no new tick this second
                    continue
                last_ts[sym] = ts
                buffer.setdefault(sym, []).append((ts, price))

            # Groww gaps → Yahoo live fallback (per-symbol rate-limited), so the
            # recording keeps ~10s resolution through a feed outage instead of a hole.
            # Only for explicitly recorded (allowlisted) symbols: with no active
            # recording the loop shadows all ~120 streamed symbols, and polling
            # Yahoo for every one of them would just get us rate-limited.
            stale = [s for s in (syms & allow)
                     if s not in fresh and (now - _yf_last.get(s, 0)) >= _YF_MIN_INTERVAL]
            if stale:
                n = await _yahoo_fallback_fill(stale[:_YF_MAX_PER_CYCLE], buffer, last_ts)
                if n:
                    logger.info("capture fallback: %d/%d stale symbols filled from Yahoo",
                                n, len(stale))

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


async def enrich_volume_yahoo(symbol: str, date_str: str) -> int:
    """Volume-enrichment fallback: build the per-minute volume sidecar from
    Yahoo's 1-min chart (last ~5 days available). Returns minutes stored."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
                params={"range": "5d", "interval": "1m", "includePrePost": "false"},
                headers=_YF_HEADERS,
            )
            r.raise_for_status()
            chart = (r.json().get("chart", {}).get("result") or [{}])[0] or {}
        timestamps = chart.get("timestamp") or []
        vols = ((chart.get("indicators", {}).get("quote") or [{}])[0] or {}).get("volume") or []
        minute_vol: dict[int, int] = {}
        for i, ts in enumerate(timestamps):
            ts = int(ts)
            if datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d") != date_str:
                continue
            try:
                v = int(vols[i] or 0)
            except (TypeError, ValueError, IndexError):
                continue
            if v > 0:
                minute_vol[(ts // 60) * 60] = v
        if not minute_vol:
            return 0
        return await asyncio.to_thread(save_minute_volume, symbol, date_str, minute_vol)
    except Exception as exc:
        logger.debug("enrich_volume_yahoo %s %s failed: %s", symbol, date_str, exc)
        return 0


# ── Intraday backfill (full-day recording regardless of start time) ───────────
# The live capture loop only ever fills the tick-store going forward, so a
# recording created mid-session would start with a hole from the 09:15 open up to
# "now". These helpers seed that earlier part of the day from 1-minute historical
# bars (Groww first, Yahoo fallback) so the recorded day is complete no matter
# when the recording was started.

async def _fetch_intraday_1m(symbol: str, date_str: str) -> list[list]:
    """1-minute OHLCV bars for `symbol` on `date_str` as [ts, o, h, l, c, v] rows,
    Groww historical first (real NSE data), Yahoo 1-min chart as fallback. Empty
    list if neither is available."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []
    start = day.replace(hour=9, minute=15, tzinfo=IST)
    end   = day.replace(hour=15, minute=30, tzinfo=IST)
    now   = datetime.now(IST)
    if end > now:
        end = now                     # today, mid-session — only up to the current minute

    # 1. Groww historical (1-min) — real ticks source of truth.
    try:
        from app.utils.groww_client import get_groww_client
        groww = get_groww_client()
        if groww:
            raw = await groww.get_historical(symbol, 1, start, end)
            rows = [c for c in (raw or []) if isinstance(c, (list, tuple)) and len(c) >= 5]
            if rows:
                return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                         int(float(c[5])) if len(c) >= 6 else 0] for c in rows]
    except Exception as exc:
        logger.debug("backfill groww fetch %s %s failed: %s", symbol, date_str, exc)

    # 2. Yahoo 1-min chart fallback.
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
                params={"range": "5d", "interval": "1m", "includePrePost": "false"},
                headers=_YF_HEADERS,
            )
            r.raise_for_status()
            chart = (r.json().get("chart", {}).get("result") or [{}])[0] or {}
        timestamps = chart.get("timestamp") or []
        q = ((chart.get("indicators", {}).get("quote") or [{}])[0] or {})
        opens, highs, lows, closes = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
        vols = q.get("volume") or []
        out: list[list] = []
        for i, ts in enumerate(timestamps):
            ts = int(ts)
            if datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d") != date_str:
                continue
            try:
                o, h, l, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
            except (TypeError, ValueError, IndexError):
                continue
            if c <= 0:
                continue
            try:
                v = int(vols[i] or 0)
            except (TypeError, ValueError, IndexError):
                v = 0
            out.append([ts, o, h, l, c, v])
        return out
    except Exception as exc:
        logger.debug("backfill yahoo fetch %s %s failed: %s", symbol, date_str, exc)
    return []


async def backfill_intraday(symbol: str, date_str: str) -> int:
    """Seed the tick-store for `symbol`/`date_str` from 1-minute historical bars so a
    recording started mid-session still holds the full day from the 09:15 open. Only
    fills the gap *before* the first live tick already captured — the live loop's real
    1-s ticks are never overwritten. OHLC shape is reconstructed with four sub-minute
    synthetic ticks per bar (open/high/low/close at distinct seconds). Also stores the
    bars' real volume sidecar in one pass. Returns the number of ticks written."""
    cov = await asyncio.to_thread(day_coverage, symbol, date_str)
    first_ts = cov.get("first_ts")            # earliest live tick already captured (or None)

    bars = await _fetch_intraday_1m(symbol, date_str)
    if not bars:
        return 0

    ticks: list[tuple[int, float]] = []
    minute_vol: dict[int, int] = {}
    for ts, o, h, l, c, v in bars:
        # Skip minutes we already cover live — never clobber real captured ticks.
        if first_ts is not None and ts >= (first_ts - 60):
            continue
        # Reconstruct OHLC order within the minute at distinct seconds so resampled
        # bars show a real body/wick instead of a flat line.
        ticks.append((ts,      o))
        ticks.append((ts + 20, h))
        ticks.append((ts + 40, l))
        ticks.append((ts + 59, c))
        if v > 0:
            minute_vol[(ts // 60) * 60] = v

    if not ticks:
        return 0
    written = await asyncio.to_thread(append_ticks, symbol, ticks)
    if minute_vol and not has_minute_volume(symbol, date_str):
        await asyncio.to_thread(save_minute_volume, symbol, date_str, minute_vol)
    if written:
        logger.info("intraday backfill: %s %s seeded %d ticks (%d min)",
                    symbol, date_str, written, len(bars),
                    extra={"log_type": "recording_event", "event": "intraday_backfill",
                           "symbol": symbol, "date": date_str, "ticks": written})
    return written


async def backfill_symbols(symbols: list[str], date_str: str, concurrency: int = 4) -> int:
    """Backfill the intraday morning gap for many symbols (bounded concurrency).
    Best-effort — a per-symbol failure is logged and skipped. Returns total ticks."""
    if not symbols:
        return 0
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(sym: str) -> int:
        async with sem:
            try:
                return await backfill_intraday(sym, date_str)
            except Exception as exc:
                logger.debug("backfill_symbols %s %s failed: %s", sym, date_str, exc)
                return 0

    results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
    return sum(n for n in results if isinstance(n, int))


def _last_completed_trading_day(now: datetime) -> str:
    """Most recent weekday whose session has already closed (post ~15:35 IST)."""
    d = now.date()
    mins = now.hour * 60 + now.minute
    if d.weekday() < 5 and mins >= (15 * 60 + 35):
        return d.isoformat()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


async def _auto_enrich_volume() -> None:
    """Off-hours sweep: for every symbol captured on the last completed trading day
    that has no volume sidecar yet, backfill real 1-min volume (Groww → Yahoo).
    Throttled to one sweep per _ENRICH_EVERY; work is batched so a big recording
    catches up over successive sweeps. Attempts are capped per symbol/day."""
    global _last_enrich
    now_ts = time.time()
    if (now_ts - _last_enrich) < _ENRICH_EVERY:
        return
    _last_enrich = now_ts

    date_str = _last_completed_trading_day(datetime.now(IST))
    try:
        pending = [s for s in await asyncio.to_thread(symbols_with_ticks, date_str)
                   if not has_minute_volume(s, date_str)]
    except Exception as exc:
        logger.debug("volume enrich scan failed: %s", exc)
        return
    if not pending:
        return

    from app.utils.redis_client import get_redis
    r = get_redis()
    done = 0
    for sym in pending[:_ENRICH_BATCH]:
        attempts_key = f"candle_capture:vol_attempts:{sym}:{date_str}"
        try:
            attempts = int(await r.get(attempts_key) or 0)
        except Exception:
            attempts = 0
        if attempts >= _ENRICH_MAX_TRIES:
            continue
        n = await enrich_volume(sym, date_str)
        if n <= 0:
            n = await enrich_volume_yahoo(sym, date_str)
        if n > 0:
            done += 1
        else:
            try:
                await r.incr(attempts_key)
                await r.expire(attempts_key, 86400 * 3)
            except Exception:
                pass
        await asyncio.sleep(0.5)     # be gentle with the data providers
    if done:
        logger.info("volume enrichment: %d/%d symbols backfilled for %s",
                    done, len(pending), date_str,
                    extra={"log_type": "recording_event", "event": "volume_enriched",
                           "date": date_str, "done": done, "pending": len(pending)})
