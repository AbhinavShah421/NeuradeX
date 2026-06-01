"""Intraday stock scanner.

Continuously sweeps the universe, scores each stock for *intraday-trading
fitness* (liquidity + volatility + a momentum signal), keeps only the names that
clear the intraday bar, ranks them, and writes the live AI watchlist to the
shared Redis key the backend already serves at /api/ai-engine/watchlist.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
import redis.asyncio as redis

from .universe import UNIVERSE

logger = logging.getLogger("stock-scanner")
IST = timezone(timedelta(hours=5, minutes=30))

_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}
_WATCHLIST_KEY = "ai_engine:watchlist"

# Intraday-fitness gates — a stock must clear these to be tradable intraday
MIN_AVG_VOLUME = float(os.getenv("SCAN_MIN_VOLUME", "300000"))   # liquidity
MIN_ATR_PCT    = float(os.getenv("SCAN_MIN_ATR_PCT", "1.2"))     # daily true range %
MIN_PRICE      = float(os.getenv("SCAN_MIN_PRICE", "30"))        # avoid illiquid penny stocks
TOP_N          = int(os.getenv("SCAN_TOP_N", "15"))
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", str(20 * 60)))   # full sweep cadence
FETCH_DELAY    = float(os.getenv("SCAN_FETCH_DELAY", "0.25"))    # be gentle on Yahoo

_redis: redis.Redis | None = None
_state = {"last_scan": None, "scanned": 0, "candidates": 0, "running": False}


async def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0"
        _redis = await redis.from_url(url, encoding="utf8", decode_responses=True)
    return _redis


# ── Indicators (pure python, no heavy deps) ───────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += d if d > 0 else 0.0
        losses += -d if d < 0 else 0.0
    ag, al = gains / period, losses / period
    return 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))


def _analyze(candles: list[dict]) -> dict | None:
    if len(candles) < 20:
        return None
    closes = [c["c"] for c in candles]
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    vols   = [c["v"] for c in candles]
    price  = closes[-1]
    if price <= 0:
        return None

    avg_vol = sum(vols[-20:]) / min(20, len(vols))
    atr     = sum(highs[i] - lows[i] for i in range(len(closes) - 14, len(closes))) / 14
    atr_pct = atr / price * 100
    range_pct = sum((highs[i] - lows[i]) / closes[i] for i in range(len(closes) - 14, len(closes))) / 14 * 100
    rsi     = _rsi(closes)
    mom     = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0.0

    fit = (avg_vol >= MIN_AVG_VOLUME) and (atr_pct >= MIN_ATR_PCT) and (price >= MIN_PRICE)

    # Intraday-suitability score: liquidity + volatility (the two things that make
    # a stock tradable intraday), nudged by momentum strength.
    liq_score = min(1.0, avg_vol / 3_000_000)
    vol_score = max(0.0, min(1.0, (atr_pct - MIN_ATR_PCT) / 3.0 + 0.3))
    mom_score = min(1.0, abs(mom) / 5.0)
    score = round(liq_score * 0.45 + vol_score * 0.40 + mom_score * 0.15, 4)

    # Directional signal
    if rsi < 40 and mom > 0:
        action = "BUY"
    elif rsi > 68 or mom < -1.5:
        action = "SELL"
    elif mom > 1.0 and rsi < 62:
        action = "BUY"
    else:
        action = "HOLD"
    confidence = round(0.4 + 0.4 * vol_score + 0.2 * mom_score, 3)

    reasoning = (f"Liquidity {avg_vol/1e6:.1f}M/day, volatility {atr_pct:.1f}% ATR, "
                 f"RSI {rsi:.0f}, momentum {mom:+.1f}% — "
                 + ("strong intraday fit" if fit else "below intraday thresholds"))

    return {
        "price": round(price, 2),
        "action": action,
        "confidence": confidence,
        "agreement": round(vol_score, 3),
        "score": score,
        "intraday_fit": fit,
        "reasoning": reasoning,
        "metrics": {
            "avg_volume": int(avg_vol),
            "atr_pct": round(atr_pct, 2),
            "range_pct": round(range_pct, 2),
            "rsi": round(rsi, 1),
            "momentum_pct": round(mom, 2),
            "liquidity_score": round(liq_score, 3),
            "volatility_score": round(vol_score, 3),
        },
    }


async def _fetch_daily(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    p2 = int(time.time())
    p1 = p2 - 70 * 86400
    try:
        r = await client.get(_YAHOO + f"{symbol}.NS",
                             params={"period1": p1, "period2": p2, "interval": "1d", "includePrePost": "false"},
                             headers=_UA, timeout=12.0)
        r.raise_for_status()
        res = (r.json().get("chart", {}).get("result") or [None])[0]
        if not res:
            return []
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        o, h, l, c, v = q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", []), q.get("volume", [])
        out = []
        for i in range(len(c)):
            try:
                cl = c[i]
                if cl is None or float(cl) <= 0:
                    continue
                out.append({"o": float(o[i] or cl), "h": float(h[i] or cl),
                            "l": float(l[i] or cl), "c": float(cl), "v": int(v[i] or 0)})
            except (TypeError, ValueError, IndexError):
                continue
        return out
    except Exception as exc:
        logger.debug("fetch %s failed: %s", symbol, exc)
        return []


async def scan_once() -> dict:
    """Sweep the whole universe once, keep intraday-fit names, store the watchlist."""
    _state["running"] = True
    candidates: list[dict] = []
    scanned = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for sym, name in UNIVERSE.items():
            candles = await _fetch_daily(client, sym)
            scanned += 1
            res = _analyze(candles)
            if res and res["intraday_fit"]:
                candidates.append({"symbol": sym, "name": name, "source": "scanner", **res})
            await asyncio.sleep(FETCH_DELAY)

    candidates.sort(key=lambda r: (r["action"] != "BUY", -r["score"]))
    watchlist = candidates[:TOP_N]

    payload = {
        "updated_at": datetime.now(IST).isoformat(),
        "scanned": scanned,
        "universe": len(UNIVERSE),
        "candidates": len(candidates),
        "items": watchlist,
    }
    try:
        r = await _get_redis()
        await r.set(_WATCHLIST_KEY, json.dumps(payload), ex=86400)
    except Exception as exc:
        logger.warning("watchlist write failed: %s", exc)

    _state.update({"last_scan": payload["updated_at"], "scanned": scanned,
                   "candidates": len(candidates), "running": False})
    logger.info("scan complete: %d scanned, %d intraday-fit, %d on watchlist",
                scanned, len(candidates), len(watchlist))
    return payload


async def scanner_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            await scan_once()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("scan loop error: %s", exc)
        await asyncio.sleep(SCAN_INTERVAL)


def get_state() -> dict:
    return dict(_state)
