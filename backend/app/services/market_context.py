"""Market context for every session decision. Information only: nothing gates on it.

Found 2026-11-25 by deep-diving the MEESHO and MFSL losses. The agents decided
on six indicators (rsi, sma5, sma20, vwap, atr, momentum) and never saw what an
intraday trader reads first:
  * where price sits against the day's open (MEESHO was +1.8% after a rejected
    spike at the prior-day high);
  * the gap against the prior close (MFSL gapped +3.9% above its prior-day
    high and faded all day);
  * the prior-day high and low, the opening range, the day's range;
  * the market itself (Nifty) and the stock's strength relative to it.

Position against the open already measures as a real effect (t +2.6 / -2.1,
both halves) on 815k labelled decisions. These fields are recorded on every
decision so the rest can be tested out of sample before anything trades on
them. Each value is computed only from bars at or before the decision bar, so
replay and backtest carry no lookahead.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_PROXY = "NIFTYBEES"        # NSE-listed Nifty 50 ETF; tracks the index intraday
_OR_BARS = 15                    # opening range = the first 15 one-minute bars
_LIVE_TTL_S = 60                 # today's Nifty bars refresh once a minute
_FAIL_TTL_S = 120                # a failed fetch is not retried by every session at once
_FETCH_TIMEOUT_S = 4.0

_cache: dict[str, tuple[float, list[dict]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _today() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


async def nifty_candles(date: str) -> list[dict]:
    """1-minute Nifty-proxy candles for `date`, shared across all sessions.
    Past days are cached for the process lifetime and today's bars for a
    minute. A failed fetch returns [] and is not retried for two minutes."""
    now = time.monotonic()
    hit = _cache.get(date)
    live = date == _today()
    if hit:
        ts, bars = hit
        ttl = (_LIVE_TTL_S if bars else _FAIL_TTL_S) if live else (float("inf") if bars else _FAIL_TTL_S)
        if now - ts < ttl:
            return bars
    lock = _locks.setdefault(date, asyncio.Lock())
    async with lock:
        hit = _cache.get(date)
        if hit and time.monotonic() - hit[0] < (_LIVE_TTL_S if live else float("inf")) and hit[1]:
            return hit[1]
        bars: list[dict] = []
        try:
            from app.data.providers import fetch_intraday
            bars, _ = await asyncio.wait_for(fetch_intraday(NIFTY_PROXY, date, 1), _FETCH_TIMEOUT_S)
        except Exception:
            bars = []
        _cache[date] = (time.monotonic(), bars or [])
        if len(_cache) > 40:                       # keep the cache bounded
            for k in sorted(_cache)[:-40]:
                _cache.pop(k, None)
        return bars or []


def _prior_level(prior_levels: dict | None, label: str) -> float | None:
    for side in ("resistance", "support"):
        for lv in (prior_levels or {}).get(side) or []:
            if lv.get("label") == label and lv.get("price"):
                return float(lv["price"])
    return None


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1.0) * 100.0, 3)


def context_features(window: list[dict], prior_levels: dict | None,
                     nifty: list[dict] | None) -> dict:
    """Context fields for the last bar of `window` (today's bars so far)."""
    if not window:
        return {}
    bar = window[-1]
    close = float(bar["close"])
    day_open = float(window[0]["open"])
    hi = max(float(c["high"]) for c in window)
    lo = min(float(c["low"]) for c in window)
    out: dict = {
        "from_open_pct": _pct(close, day_open),
        "range_pos": round((close - lo) / (hi - lo), 3) if hi > lo else None,
    }
    pdh, pdl, pdc = (_prior_level(prior_levels, k) for k in ("PDH", "PDL", "PDC"))
    out["gap_pct"] = _pct(day_open, pdc)
    out["pdh_dist_pct"] = _pct(close, pdh)
    out["pdl_dist_pct"] = _pct(close, pdl)
    if len(window) > _OR_BARS:
        orb = window[:_OR_BARS]
        or_hi = max(float(c["high"]) for c in orb)
        or_lo = min(float(c["low"]) for c in orb)
        # > 0: % above the range high; < 0: % below the range low; 0: inside.
        out["or_pos_pct"] = (_pct(close, or_hi) if close > or_hi
                             else _pct(close, or_lo) if close < or_lo else 0.0)
    else:
        out["or_pos_pct"] = None
    seen = [c for c in (nifty or []) if c.get("time") and c["time"] <= bar.get("time", "")]
    if seen:
        n_open = float(seen[0]["open"])
        n_close = float(seen[-1]["close"])
        out["nifty_from_open_pct"] = _pct(n_close, n_open)
        out["rs_nifty_pct"] = (round(out["from_open_pct"] - out["nifty_from_open_pct"], 3)
                               if out["from_open_pct"] is not None and out["nifty_from_open_pct"] is not None
                               else None)
    else:
        out["nifty_from_open_pct"] = None
        out["rs_nifty_pct"] = None
    return out


async def decision_context(date: str | None, window: list[dict], prior_levels: dict | None) -> dict:
    """context_features plus the Nifty fetch. Never raises; {} on any failure."""
    try:
        nifty = await nifty_candles(date) if date else []
        return context_features(window, prior_levels, nifty)
    except Exception:
        return {}
