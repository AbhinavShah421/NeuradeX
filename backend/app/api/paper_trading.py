"""
Paper Trading Module — live AI-assisted day trading using real-time Groww data.

Runs against today's live market. Server always derives current_time from
datetime.now(IST), so there is no date/time input required from the client.

Endpoints:
  GET  /paper-trading/status          — market status, current IST time
  POST /paper-trading/start           — start session (candles from 09:15 to now)
  POST /paper-trading/step            — advance one candle (real IST time)
  GET  /paper-trading/tick/{symbol}   — live LTP + AI signal (polled every N secs)
  POST /paper-trading/place-order     — place a real Groww order
"""
import time as _time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.backtest import (
    IST,
    _MARKET_OPEN_MINUTES,
    _SQUAREOFF_MINUTES,
    _compute_metrics,
    _intraday_indicators,
    _llm_decide,
    _minutes_to_time,
    _tech_signal,
    _time_to_minutes,
)
from app.config import settings
from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger
from app.api.auth import get_current_user

logger = get_logger(__name__)
router = APIRouter()

_MARKET_CLOSE_MINUTES = 15 * 60 + 30  # 15:30 IST

# ── Server-side candle cache (Groww historical — currently empty for live day) ─
_candle_cache: dict[str, tuple[list, float, str]] = {}
_CANDLE_CACHE_TTL = 30

# ── Tick-driven candle builder ────────────────────────────────────────────────
# Since Groww's /historical/candle/range only serves completed days (not today),
# we build 1-minute OHLCV candles from the LTP ticks we already poll every second.
# symbol → (current_minute: int, prices: list[float], first_ts: float)
_tick_minute: dict[str, tuple[int, list[float], float]] = {}
# symbol → list of completed 1-min candles built from ticks
_tick_candles: dict[str, list[dict]] = {}

# ── Yahoo Finance candle cache ────────────────────────────────────────────────
# Today's 1-min intraday history from Yahoo Finance (SYMBOL.NS).
# Used as the historical base; Groww tick candles override recent minutes.
# symbol → (candles: list[dict], cached_at: float)
_yahoo_candles: dict[str, tuple[list, float]] = {}
_YAHOO_CACHE_TTL = 15  # seconds — Yahoo's 1-min data is current-minute fresh, so
                       # refresh it every ~15s to keep the live candle gap small


# ── Time helpers ───────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _current_ist_minutes() -> int:
    n = _now_ist()
    return n.hour * 60 + n.minute


def _current_candle_time() -> str:
    """HH:MM of the most recently *completed* 1-min candle as of now."""
    m = _current_ist_minutes()
    if m <= _MARKET_OPEN_MINUTES:
        return _minutes_to_time(_MARKET_OPEN_MINUTES)
    candle_m = min(m - 1, _SQUAREOFF_MINUTES)
    return _minutes_to_time(max(_MARKET_OPEN_MINUTES, candle_m))


def _seconds_until_next_candle(current_candle_hhmm: str) -> int:
    """Seconds from right now until the next 1-min candle boundary."""
    next_m  = _time_to_minutes(current_candle_hhmm) + 1
    now_ist = _now_ist()
    next_dt = now_ist.replace(
        hour=next_m // 60,
        minute=next_m % 60,
        second=0, microsecond=0,
    )
    diff = (next_dt - now_ist).total_seconds()
    return max(0, int(diff))


def _is_market_open() -> bool:
    n = _now_ist()
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return _MARKET_OPEN_MINUTES <= m <= _MARKET_CLOSE_MINUTES


def _market_status_label() -> str:
    n = _now_ist()
    if n.weekday() >= 5:
        return "weekend"
    m = n.hour * 60 + n.minute
    if m < _MARKET_OPEN_MINUTES:
        return "pre_market"
    if m > _MARKET_CLOSE_MINUTES:
        return "closed"
    return "open"


# ── Models ─────────────────────────────────────────────────────────────────────

class PaperTradingStartRequest(BaseModel):
    symbol:  str
    capital: float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    model:   Optional[str] = None


class PaperTradingStepRequest(BaseModel):
    symbol:       str
    capital:      float
    cash:         float
    position:     str = "NONE"
    quantity:     int = 0
    entry_price:  float = 0.0
    entry_time:   Optional[str] = None
    trades:       list[dict] = []
    model:        Optional[str] = None


# ── Tick-candle accumulator ────────────────────────────────────────────────────

def _accumulate_tick(symbol: str, ltp: float, ts: float) -> None:
    """Record one LTP tick. Whenever the clock rolls into a new minute,
    the previous minute's ticks are sealed as a completed 1-min candle.
    """
    now_ist  = datetime.fromtimestamp(ts, tz=IST)
    cur_min  = now_ist.hour * 60 + now_ist.minute

    if symbol not in _tick_minute:
        _tick_minute[symbol] = (cur_min, [ltp], ts)
        return

    prev_min, prices, first_ts = _tick_minute[symbol]

    if cur_min == prev_min:
        prices.append(ltp)
    else:
        # Seal the completed candle for prev_min
        if prices and _MARKET_OPEN_MINUTES <= prev_min <= _SQUAREOFF_MINUTES:
            prev_dt = datetime.fromtimestamp(first_ts, tz=IST)
            candle  = {
                "time":      f"{prev_min // 60:02d}:{prev_min % 60:02d}",
                "timestamp": int(first_ts),
                "open":      prices[0],
                "high":      max(prices),
                "low":       min(prices),
                "close":     prices[-1],
                "volume":    0,  # LTP polling has no volume
            }
            if symbol not in _tick_candles:
                _tick_candles[symbol] = []
            # Avoid duplicate candles (idempotent)
            if not _tick_candles[symbol] or _tick_candles[symbol][-1]["time"] != candle["time"]:
                _tick_candles[symbol].append(candle)
                logger.info(
                    "1-min candle sealed from ticks",
                    extra={"log_type": "paper_trading_event", "event": "tick_candle_sealed",
                           "symbol": symbol, "time": candle["time"], "close": prices[-1]},
                )
        _tick_minute[symbol] = (cur_min, [ltp], ts)


def _get_tick_candles(symbol: str, ltp: float, ts: float) -> list[dict]:
    """Return all completed tick-candles plus a live in-progress candle for the
    current minute (with the latest LTP as its close).
    """
    completed = list(_tick_candles.get(symbol, []))

    now_ist = datetime.fromtimestamp(ts, tz=IST)
    cur_min = now_ist.hour * 60 + now_ist.minute
    if not (_MARKET_OPEN_MINUTES <= cur_min <= _SQUAREOFF_MINUTES):
        return completed

    if symbol in _tick_minute and ltp > 0:
        _, prices, first_ts = _tick_minute[symbol]
        if prices:
            live = {
                "time":      f"{cur_min // 60:02d}:{cur_min % 60:02d}",
                "timestamp": int(first_ts),
                "open":      prices[0],
                "high":      max(max(prices), ltp),
                "low":       min(min(prices), ltp),
                "close":     ltp,
                "volume":    0,
            }
            # Replace last entry if it's also the current minute (avoid dup)
            if completed and completed[-1]["time"] == live["time"]:
                completed[-1] = live
            else:
                completed.append(live)
    return completed


# ── 1-minute candle fetch + cache ─────────────────────────────────────────────

def _parse_groww_candles(raw: list, up_to_m: int) -> list[dict]:
    """Parse Groww candle data — handles both list-of-lists and list-of-dicts formats."""
    parsed = []
    for c in raw:
        try:
            if isinstance(c, list) and len(c) >= 5:
                # [timestamp, open, high, low, close, volume?]
                ts     = int(c[0])
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                vol    = int(c[5]) if len(c) >= 6 else 0
            elif isinstance(c, dict):
                # {timestamp/time, open, high, low, close, volume}
                ts_raw = (c.get("timestamp") or c.get("ts") or
                          c.get("epoch") or c.get("date") or 0)
                ts = int(ts_raw)
                o  = float(c.get("open")  or c.get("o") or 0)
                h  = float(c.get("high")  or c.get("h") or 0)
                l  = float(c.get("low")   or c.get("l") or 0)
                cl = float(c.get("close") or c.get("c") or 0)
                vol = int(c.get("volume") or c.get("v") or 0)
            else:
                continue

            if ts <= 0 or cl <= 0:
                continue

            dt_ist = datetime.fromtimestamp(ts, tz=IST)
            cmin   = dt_ist.hour * 60 + dt_ist.minute
            if _MARKET_OPEN_MINUTES <= cmin <= up_to_m:
                parsed.append({
                    "time":      dt_ist.strftime("%H:%M"),
                    "timestamp": ts,
                    "open":   o,
                    "high":   h,
                    "low":    l,
                    "close":  cl,
                    "volume": vol,
                })
        except (TypeError, ValueError, IndexError):
            continue
    return parsed


async def _fetch_candles_groww(
    symbol: str, up_to_time: str, intervals: tuple[int, ...]
) -> tuple[list, str]:
    """Try each interval in order; return the first non-empty Groww result.
    Never falls back to simulation — returns [], 'no_data' if all fail.
    """
    today   = _today_str()
    date    = datetime.strptime(today, "%Y-%m-%d")
    up_to_m = _time_to_minutes(up_to_time)
    groww   = get_groww_client()

    if not groww:
        return [], "no_groww_client"

    for interval in intervals:
        try:
            logger.info(
                "Calling Groww get_historical for paper trading",
                extra={
                    "log_type": "groww_call",
                    "caller": "paper_trading.fetch_candles",
                    "method": "get_historical",
                    "symbol": symbol,
                    "interval_minutes": interval,
                    "up_to": up_to_time,
                },
            )
            raw = await groww.get_historical(
                symbol, interval, date, date.replace(hour=23, minute=59)
            )
            if raw and len(raw) >= 1:
                parsed = _parse_groww_candles(raw, up_to_m)
                if parsed:
                    logger.info(
                        "Candles fetched",
                        extra={"log_type": "paper_trading_event", "event": "candle_ok",
                               "symbol": symbol, "interval": interval, "count": len(parsed)},
                    )
                    return parsed, "groww"
                # Log raw sample so we can diagnose format mismatches
                logger.warning(
                    "Groww returned data but parse yielded 0 candles",
                    extra={"log_type": "paper_trading_event", "event": "parse_empty",
                           "symbol": symbol, "interval": interval,
                           "raw_len": len(raw),
                           "raw_sample": str(raw[:2])[:400]},
                )
        except Exception as exc:
            logger.warning(
                "Groww get_historical failed",
                extra={"log_type": "paper_trading_event", "event": "candle_fetch_failed",
                       "symbol": symbol, "interval": interval, "error": str(exc)},
            )

    logger.warning(
        "No candle data from Groww across all intervals",
        extra={"log_type": "paper_trading_event", "event": "no_candle_data",
               "symbol": symbol, "intervals_tried": list(intervals)},
    )
    return [], "no_data"


async def _fetch_candles_for_start(symbol: str, up_to_time: str) -> tuple[list, str]:
    """Initial session bootstrap.

    Priority:
      1. Groww historical (works for past dates; usually empty for today's live session)
      2. Yahoo Finance — provides today's complete 1-min history from 09:15 IST
      3. Tick candles built from LTP polls (accumulated since backend started)
    """
    # 1. Groww historical
    candles, src = await _fetch_candles_groww(symbol, up_to_time, (5, 10, 15, 30, 60, 3, 2, 1))
    if candles:
        return candles, src

    # 2. Yahoo Finance for today's intraday history
    yahoo = await _get_yahoo_cached(symbol, up_to_time)
    if yahoo:
        return yahoo, "yahoo_finance"

    return [], "no_data"


async def _fetch_1min_candles(symbol: str, up_to_time: str) -> tuple[list, str]:
    """Ongoing tick / step fetches: always try finest resolution first."""
    return await _fetch_candles_groww(symbol, up_to_time, (1, 2, 3, 5))


async def _fetch_candles_yahoo(symbol: str, up_to_time: str) -> tuple[list, str]:
    """Fetch today's 1-min intraday candles from Yahoo Finance (NSE).

    Yahoo Finance has a ~2-min delay but provides the full day history from
    09:15 IST — used as a historical base when Groww historical returns empty.
    """
    up_to_m = _time_to_minutes(up_to_time)
    yahoo_sym = f"{symbol}.NS"
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + yahoo_sym
        params = {
            "range": "1d",
            "interval": "1m",
            "includePrePost": "false",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()

        result = data.get("chart", {}).get("result") or []
        if not result:
            return [], "yahoo_no_data"

        chart = result[0]
        timestamps = chart.get("timestamp") or []
        quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
        opens   = quote.get("open",   [])
        highs   = quote.get("high",   [])
        lows    = quote.get("low",    [])
        closes  = quote.get("close",  [])
        volumes = quote.get("volume", [])

        candles = []
        for i, ts in enumerate(timestamps):
            try:
                o = float(opens[i]  or 0)
                h = float(highs[i]  or 0)
                l = float(lows[i]   or 0)
                c = float(closes[i] or 0)
                v = int(volumes[i]  or 0)
            except (TypeError, ValueError, IndexError):
                continue
            if c <= 0:
                continue
            dt_ist = datetime.fromtimestamp(ts, tz=IST)
            cmin   = dt_ist.hour * 60 + dt_ist.minute
            if _MARKET_OPEN_MINUTES <= cmin <= up_to_m:
                candles.append({
                    "time":      dt_ist.strftime("%H:%M"),
                    "timestamp": int(ts),
                    "open":      round(o, 2),
                    "high":      round(h, 2),
                    "low":       round(l, 2),
                    "close":     round(c, 2),
                    "volume":    v,
                })

        logger.info(
            "Yahoo Finance candles fetched",
            extra={"log_type": "paper_trading_event", "event": "yahoo_ok",
                   "symbol": symbol, "count": len(candles)},
        )
        return candles, "yahoo_finance"

    except Exception as exc:
        logger.warning(
            "Yahoo Finance fetch failed",
            extra={"log_type": "paper_trading_event", "event": "yahoo_failed",
                   "symbol": symbol, "error": str(exc)},
        )
        return [], "yahoo_failed"


async def _get_yahoo_cached(symbol: str, up_to_time: str) -> list:
    """Return cached Yahoo candles, refreshing if stale."""
    now_ts = _time.monotonic()
    cached = _yahoo_candles.get(symbol)
    if cached and (now_ts - cached[1]) < _YAHOO_CACHE_TTL:
        return cached[0]
    candles, _ = await _fetch_candles_yahoo(symbol, up_to_time)
    if candles:
        _yahoo_candles[symbol] = (candles, now_ts)
    return candles


def _get_merged_candles(symbol: str, ltp: float, ts: float) -> list[dict]:
    """Merge Yahoo Finance historical base with real Groww tick candles.

    Yahoo Finance provides complete history from 09:15 IST.
    Groww tick candles override any Yahoo bar for the same minute (more accurate).
    Appends a live in-progress bar for the current minute using the latest LTP.
    """
    yahoo_base = (_yahoo_candles.get(symbol) or ([], 0))[0]
    tick_done  = {c["time"]: c for c in _tick_candles.get(symbol, [])}

    # Start from Yahoo base, replacing minutes where we have real Groww ticks
    merged: list[dict] = []
    for c in yahoo_base:
        merged.append(tick_done.get(c["time"], c))

    # Append any Groww tick candles whose time is beyond Yahoo's range
    yahoo_times = {c["time"] for c in yahoo_base}
    for c in _tick_candles.get(symbol, []):
        if c["time"] not in yahoo_times:
            merged.append(c)

    # Live in-progress bar for the current minute
    if ltp > 0 and symbol in _tick_minute:
        now_ist = datetime.fromtimestamp(ts, tz=IST)
        cur_min = now_ist.hour * 60 + now_ist.minute
        if _MARKET_OPEN_MINUTES <= cur_min <= _SQUAREOFF_MINUTES:
            _, prices, first_ts = _tick_minute[symbol]
            if prices:
                live_time = f"{cur_min // 60:02d}:{cur_min % 60:02d}"
                live: dict = {
                    "time":      live_time,
                    "timestamp": int(first_ts),
                    "open":      prices[0],
                    "high":      max(max(prices), ltp),
                    "low":       min(min(prices), ltp),
                    "close":     ltp,
                    "volume":    0,
                }
                if merged and merged[-1]["time"] == live_time:
                    merged[-1] = live
                else:
                    merged.append(live)

    merged.sort(key=lambda c: c["time"])
    return merged


async def _get_cached_candles(symbol: str) -> tuple[list, str]:
    """Return today's 1-min candles, refreshing cache at most every 30s."""
    now_ts = _time.monotonic()
    cached = _candle_cache.get(symbol)
    if cached and (now_ts - cached[1]) < _CANDLE_CACHE_TTL:
        return cached[0], cached[2]

    current_time = _current_candle_time()
    candles, src = await _fetch_1min_candles(symbol, current_time)
    if candles:
        _candle_cache[symbol] = (candles, now_ts, src)
    return candles or [], src


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def paper_trading_status():
    """Return current market status and IST time — no auth required."""
    now           = _now_ist()
    current_time  = _current_candle_time()
    mstatus       = _market_status_label()
    secs          = _seconds_until_next_candle(current_time) if mstatus == "open" else 0
    next_m        = _time_to_minutes(current_time) + 1
    is_closed     = next_m > _SQUAREOFF_MINUTES

    return {
        "status": "success",
        "data": {
            "ist_now":               now.strftime("%H:%M:%S"),
            "ist_date":              now.strftime("%Y-%m-%d"),
            "market_status":         mstatus,
            "is_market_open":        mstatus == "open",
            "current_candle_time":   current_time,
            "next_candle_in_secs":   secs,
            "candle_interval_mins":  1,
            "is_session_ended":      is_closed or mstatus != "open",
        },
    }


@router.post("/start")
async def paper_trading_start(req: PaperTradingStartRequest):
    """Start a live paper trading session.

    Fetches candles from market open to the current completed 5-min candle,
    runs the AI agent on that candle, and returns initial session state.
    The client stores this state and passes it back each step call.
    """
    symbol  = req.symbol.upper()
    mstatus = _market_status_label()

    if mstatus == "weekend":
        raise HTTPException(400, "Market is closed on weekends.")
    if mstatus == "pre_market":
        now = _now_ist()
        raise HTTPException(
            400,
            f"Market hasn't opened yet. NSE opens at 09:15 IST "
            f"(current time: {now.strftime('%H:%M IST')})."
        )

    today        = _today_str()
    current_time = _current_candle_time()
    model        = req.model or getattr(settings, "LLM_MODEL", "llama3.2")

    candles, data_source = await _fetch_candles_for_start(symbol, current_time)
    if not candles:
        # All sources exhausted — fall back to whatever ticks have accumulated so far
        now_ts  = _now_ist().timestamp()
        candles = _get_merged_candles(symbol, 0.0, now_ts)
        data_source = "groww_ticks" if candles else "no_data"

    cash        = req.capital
    position    = "NONE"
    quantity    = 0
    entry_price = 0.0
    entry_time  = None
    trades: list[dict] = []
    trade_executed = None
    dec         = {"action": "HOLD", "confidence": 0, "reason": "Waiting for candle data.", "quantity": 0}
    ind: dict   = {}

    if candles:
        idx    = len(candles) - 1
        candle = candles[idx]
        ind    = _intraday_indicators(candles, idx)
        signal = _tech_signal(ind, "NONE", candle, 0.0)
        dec    = await _llm_decide(
            symbol, today, candle, ind,
            "NONE", 0.0, 0.0,
            req.capital, signal,
            candles[max(0, idx - 5):idx + 1], model,
        )
    else:
        idx    = -1
        candle = {}

    if dec["action"] == "BUY" and candles:
        qty  = dec.get("quantity") or max(1, int(cash * 0.95 / candle["close"]))
        cost = qty * candle["close"]
        if cost <= cash:
            cash        -= cost
            position     = "LONG"
            quantity     = qty
            entry_price  = candle["close"]
            entry_time   = candle["time"]
            trade_executed = {"action": "BUY", "price": candle["close"], "quantity": qty, "pnl": None, "time": candle["time"]}
            trades.append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": candle["close"], "quantity": qty,
                "confidence": dec["confidence"], "reason": dec["reason"],
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
            })

    next_minutes     = _time_to_minutes(current_time) + 1
    is_session_ended = next_minutes > _SQUAREOFF_MINUTES or mstatus != "open"
    secs_until_next  = _seconds_until_next_candle(current_time) if not is_session_ended else 0

    logger.info(
        "Paper trading session started",
        extra={
            "log_type": "paper_trading_event",
            "event": "start",
            "symbol": symbol,
            "capital": req.capital,
            "current_time": current_time,
            "candles_fetched": len(candles),
            "data_source": data_source,
            "agent_action": dec["action"],
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":          symbol,
            "date":            today,
            "current_time":    current_time,
            "candles":         candles,
            "latest_candle":   candle,
            "indicators":      ind,
            "agent_decision":  dec,
            "trade_executed":  trade_executed,
            "position": {
                "status":      position,
                "entry_price": entry_price,
                "quantity":    quantity,
                "entry_time":  entry_time,
                "current_pnl": 0.0,
            },
            "cash":              round(cash, 2),
            "capital":           req.capital,
            "trades":            trades,
            "metrics":           _compute_metrics(cash, req.capital, trades),
            "is_session_ended":  is_session_ended,
            "secs_until_next":   secs_until_next,
            "data_source":       data_source,
            "model_used":        model,
            "candle_count":      len(candles),
        },
    }


@router.post("/step")
async def paper_trading_step(req: PaperTradingStepRequest):
    """Advance the paper trading session by fetching the next live candle.

    The server determines the current candle time from datetime.now(IST),
    so the client does not need to send a timestamp.
    Session state (cash, position, trades) is passed by the client and
    updated server-side before being returned.
    """
    symbol  = req.symbol.upper()
    mstatus = _market_status_label()
    today   = _today_str()

    current_time     = _current_candle_time()
    next_minutes     = _time_to_minutes(current_time) + 1
    is_session_ended = next_minutes > _SQUAREOFF_MINUTES or mstatus not in ("open",)
    secs_until_next  = _seconds_until_next_candle(current_time) if not is_session_ended else 0

    now_ts  = _now_ist().timestamp()
    candles = _get_merged_candles(symbol, 0.0, now_ts)
    data_source = "yahoo+groww" if (_yahoo_candles.get(symbol) or ([], 0))[0] else ("groww_ticks" if candles else "no_data")
    if not candles:
        raise HTTPException(503, f"No candle data for {symbol} yet. Retry in a few seconds.")

    model       = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    cash        = req.cash
    position    = req.position
    quantity    = req.quantity
    entry_price = req.entry_price
    entry_time  = req.entry_time
    trades      = list(req.trades)

    idx    = len(candles) - 1
    candle = candles[idx]
    ind    = _intraday_indicators(candles, idx)

    force_squareoff = is_session_ended and position == "LONG"
    signal = -1 if force_squareoff else _tech_signal(ind, position, candle, entry_price)

    unrealised = (candle["close"] - entry_price) * quantity if position == "LONG" else 0.0
    dec = await _llm_decide(
        symbol, today, candle, ind,
        position, entry_price, unrealised,
        cash, signal,
        candles[max(0, idx - 5):idx + 1], model,
    )

    if force_squareoff:
        dec["action"]     = "SELL"
        dec["reason"]     = "Session end — all positions squared off."
        dec["confidence"] = 99

    trade_executed = None

    if dec["action"] == "BUY" and position == "NONE":
        qty  = dec.get("quantity") or max(1, int(cash * 0.95 / candle["close"]))
        cost = qty * candle["close"]
        if cost <= cash:
            cash        -= cost
            position     = "LONG"
            quantity     = qty
            entry_price  = candle["close"]
            entry_time   = candle["time"]
            trade_executed = {"action": "BUY", "price": candle["close"], "quantity": qty, "pnl": None, "time": candle["time"]}
            trades.append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": candle["close"], "quantity": qty,
                "confidence": dec["confidence"], "reason": dec["reason"],
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
            })

    elif dec["action"] == "SELL" and position == "LONG":
        revenue = quantity * candle["close"]
        pnl     = revenue - quantity * entry_price
        cash   += revenue
        pnl_pct = round(pnl / (quantity * entry_price) * 100, 2) if entry_price > 0 else 0.0
        trade_executed = {"action": "SELL", "price": candle["close"], "quantity": quantity, "pnl": round(pnl, 2), "time": candle["time"]}
        trades.append({
            "time": candle["time"], "timestamp": candle.get("timestamp", 0),
            "action": "SELL", "price": candle["close"], "quantity": quantity,
            "confidence": dec["confidence"], "reason": dec["reason"],
            "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
            "candle_index": idx, "indicators": ind,
        })
        position    = "NONE"
        quantity    = 0
        entry_price = 0.0
        entry_time  = None

    logger.info(
        "Paper trading step",
        extra={
            "log_type": "paper_trading_event",
            "event": "step",
            "symbol": symbol,
            "current_time": current_time,
            "data_source": data_source,
            "agent_action": dec["action"],
            "position": position,
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":          symbol,
            "date":            today,
            "current_time":    current_time,
            "candles":         candles,
            "latest_candle":   candle,
            "indicators":      ind,
            "agent_decision":  dec,
            "trade_executed":  trade_executed,
            "position": {
                "status":      position,
                "entry_price": entry_price,
                "quantity":    quantity,
                "entry_time":  entry_time,
                "current_pnl": round(unrealised, 2) if position == "LONG" else 0.0,
            },
            "cash":              round(cash, 2),
            "capital":           req.capital,
            "trades":            trades,
            "metrics":           _compute_metrics(cash, req.capital, trades),
            "is_session_ended":  is_session_ended,
            "secs_until_next":   secs_until_next,
            "data_source":       data_source,
            "model_used":        model,
        },
    }


# ── Tick endpoint — polled every N seconds by the frontend ────────────────────

@router.get("/tick/{symbol}")
async def paper_trading_tick(
    symbol: str,
    position:    str   = Query("NONE"),
    entry_price: float = Query(0.0),
    quantity:    int   = Query(0),
    user: dict = Depends(get_current_user),
):
    """Live LTP + technical signal — called every N seconds (configurable).

    LTP is a single lightweight Groww call on every request.
    Candle data uses a 60-second server-side cache so the Groww historical
    API is not hammered at 1-second polling rates.
    """
    symbol = symbol.upper()
    groww  = get_groww_client()
    now    = _now_ist()

    # ── 1. Full quote from Groww ──────────────────────────────────────────────
    ltp:           Optional[float] = None
    prev_close:    Optional[float] = None
    day_open:      Optional[float] = None
    day_high:      Optional[float] = None
    day_low:       Optional[float] = None
    day_volume:    Optional[int]   = None
    bid_price:     Optional[float] = None
    ask_price:     Optional[float] = None
    upper_circuit: Optional[float] = None
    lower_circuit: Optional[float] = None
    change      = 0.0
    change_pct  = 0.0
    quote_source = "cache"

    if groww:
        try:
            raw = await groww.get_quote(symbol)
            # Groww may return camelCase or snake_case — handle both
            def _f(r: dict, *keys) -> Optional[float]:
                for k in keys:
                    v = r.get(k)
                    if v is not None:
                        try: return float(v)
                        except (TypeError, ValueError): pass
                return None

            ltp           = _f(raw, "ltp", "lastPrice", "last_price", "lastTradedPrice") or None
            prev_close    = _f(raw, "close", "prevClose", "prev_close", "previousClose")
            day_open      = _f(raw, "open", "openPrice", "open_price", "dayOpen")
            day_high      = _f(raw, "high", "highPrice", "high_price", "dayHigh", "52WeekHigh")
            day_low       = _f(raw, "low",  "lowPrice",  "low_price",  "dayLow",  "52WeekLow")
            bid_price     = _f(raw, "buyPrice",  "buy_price",  "bidPrice",  "bid_price")
            ask_price     = _f(raw, "sellPrice", "sell_price", "askPrice",  "ask_price")
            upper_circuit = _f(raw, "upperCircuit", "upper_circuit", "upperCircuitLimit")
            lower_circuit = _f(raw, "lowerCircuit", "lower_circuit", "lowerCircuitLimit")
            change     = float(raw.get("change") or raw.get("netChange") or raw.get("net_change") or 0)
            change_pct = float(raw.get("pChange") or raw.get("changePercent") or raw.get("change_percent") or raw.get("percentChange") or 0)

            vol_raw = raw.get("volume") or raw.get("volumeTraded") or raw.get("volume_traded_today") or raw.get("totalTradedVolume")
            if vol_raw is not None:
                try: day_volume = int(vol_raw)
                except (TypeError, ValueError): pass

            if not ltp:
                # Quote returned but no ltp — fall back to LTP endpoint
                ltp_raw = await groww.get_ltp([symbol])
                key   = f"NSE_{symbol}"
                entry = ltp_raw.get(key) or ltp_raw.get(symbol)
                if isinstance(entry, dict):
                    ltp = float(entry.get("ltp") or entry.get("last_trade_price") or 0) or None
                elif isinstance(entry, (int, float)):
                    ltp = float(entry)

            quote_source = "groww"
            logger.info(
                "Quote fetched",
                extra={"log_type": "paper_trading_event", "event": "quote_ok",
                       "symbol": symbol, "ltp": ltp},
            )
        except Exception as exc:
            logger.warning(
                "Quote fetch failed",
                extra={"log_type": "paper_trading_event", "event": "quote_failed",
                       "symbol": symbol, "error": str(exc)},
            )

    # ── 2. Accumulate tick + build merged candle set ─────────────────────────
    price = ltp if ltp and ltp > 0 else 0.0
    ts    = now.timestamp()

    if price > 0:
        _accumulate_tick(symbol, price, ts)

    # Merged candles: Yahoo Finance history (09:15 → now-2min) + Groww tick candles
    # (real prices for recent minutes) + live in-progress bar with current LTP.
    # Always refresh — _get_yahoo_cached has a 15s TTL, so this only hits Yahoo's
    # API when stale. Fetching once per symbol froze the candle window otherwise.
    current_time_for_yahoo = _current_candle_time()
    try:
        await _get_yahoo_cached(symbol, current_time_for_yahoo)
    except Exception:
        pass

    candles     = _get_merged_candles(symbol, price, ts)
    data_source = "yahoo+groww" if (_yahoo_candles.get(symbol) or ([], 0))[0] else ("groww_ticks" if candles else "no_data")

    if not candles:
        # No data at all — return live price only
        return {
            "status": "success",
            "data": {
                "symbol":        symbol,
                "price":         round(price, 2),
                "change":        round(change, 2),
                "change_pct":    round(change_pct, 4),
                "prev_close":    round(prev_close, 2) if prev_close else None,
                "day_open":      round(day_open,   2) if day_open   else None,
                "day_high":      round(day_high,   2) if day_high   else None,
                "day_low":       round(day_low,    2) if day_low    else None,
                "day_volume":    day_volume,
                "bid_price":     round(bid_price,  2) if bid_price  else None,
                "ask_price":     round(ask_price,  2) if ask_price  else None,
                "signal":        "HOLD",
                "signal_int":    0,
                "indicators":    {},
                "ist_time":      now.strftime("%H:%M:%S"),
                "timestamp":     int(now.timestamp()),
                "candle_time":   "",
                "candle_count":  0,
                "data_source":   data_source,
                "quote_source":  quote_source,
                "unrealised_pnl": 0.0,
            },
        }

    working = candles

    # If day_high/low not from quote, derive from tick candles
    if not day_high: day_high = max(c["high"]  for c in candles)
    if not day_low:  day_low  = min(c["low"]   for c in candles)
    if not day_open: day_open = candles[0]["open"] if candles else None
    if not price:    price    = float(candles[-1]["close"])

    # ── 3. Technical signal ───────────────────────────────────────────────────
    ind          = _intraday_indicators(working, len(working) - 1)
    signal       = _tech_signal(ind, position, working[-1], entry_price)
    signal_label = {1: "BUY", -1: "SELL", 0: "HOLD"}.get(signal, "HOLD")
    unrealised   = round((price - entry_price) * quantity, 2) if position == "LONG" and quantity > 0 else 0.0

    return {
        "status": "success",
        "data": {
            "symbol":          symbol,
            "price":           round(price, 2),
            "change":          round(change, 2),
            "change_pct":      round(change_pct, 4),
            "prev_close":      round(prev_close, 2) if prev_close else None,
            "day_open":        round(day_open,   2) if day_open   else None,
            "day_high":        round(day_high,   2) if day_high   else None,
            "day_low":         round(day_low,    2) if day_low    else None,
            "day_volume":      day_volume,
            "bid_price":       round(bid_price,  2) if bid_price  else None,
            "ask_price":       round(ask_price,  2) if ask_price  else None,
            "upper_circuit":   round(upper_circuit, 2) if upper_circuit else None,
            "lower_circuit":   round(lower_circuit, 2) if lower_circuit else None,
            "signal":          signal_label,
            "signal_int":      signal,
            "indicators":      ind,
            "ist_time":        now.strftime("%H:%M:%S"),
            "timestamp":       int(now.timestamp()),
            "candle_time":     working[-1].get("time", ""),
            "candle_count":    len(candles),
            "candles":         candles,
            "quote_source":    quote_source,
            "data_source":     data_source,
            "unrealised_pnl":  unrealised,
        },
    }


# ── Place order ───────────────────────────────────────────────────────────────

_MAX_ORDER_QTY = 10_000

class PlaceOrderRequest(BaseModel):
    symbol:     str
    action:     str        # BUY or SELL
    quantity:   int = Field(..., gt=0, le=_MAX_ORDER_QTY)
    order_type: str = "MARKET"
    price:      float = 0.0
    product:    str = "MIS"  # intraday
    exchange:   str = "NSE"


@router.post("/place-order")
async def place_order(req: PlaceOrderRequest, user: dict = Depends(get_current_user)):
    """Place a real Groww order after user confirms the AI signal."""
    if not _is_market_open():
        raise HTTPException(400, "Market is currently closed — orders can only be placed during NSE trading hours (09:15–15:30 IST, Mon–Fri)")

    symbol = req.symbol.upper()
    groww  = get_groww_client()

    if not groww:
        raise HTTPException(503, "Groww client not initialised.")
    if req.action.upper() not in ("BUY", "SELL"):
        raise HTTPException(400, "action must be BUY or SELL")

    try:
        result = await groww.place_order(
            symbol           = symbol,
            quantity         = req.quantity,
            transaction_type = req.action.upper(),
            order_type       = req.order_type.upper(),
            price            = req.price,
            product          = req.product.upper(),
            exchange         = req.exchange.upper(),
        )
        logger.info(
            "Order placed via paper trading",
            extra={"log_type": "paper_trading_event", "event": "order_placed",
                   "symbol": symbol, "action": req.action.upper(),
                   "quantity": req.quantity},
        )
        return {
            "status": "success",
            "data": {
                "symbol":         symbol,
                "action":         req.action.upper(),
                "quantity":       req.quantity,
                "order_type":     req.order_type,
                "groww_response": result,
                "placed_at":      _now_ist().strftime("%H:%M:%S IST"),
            },
        }
    except Exception as exc:
        logger.error(
            "Order placement failed",
            extra={"log_type": "paper_trading_event", "event": "order_failed",
                   "symbol": symbol, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(500, f"Order failed: {exc}")
