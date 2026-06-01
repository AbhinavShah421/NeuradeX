"""Intraday stock scanner.

Continuously sweeps the universe and scores each stock for *intraday-trading
fitness* using a broad set of market indicators that move a stock's price —
liquidity, volatility, trend (SMA/MACD), momentum, relative volume, the opening
gap, where price sits in its recent range, and the prevailing market (NIFTY)
regime. It keeps only the names that clear the intraday bar, ranks them, and
writes the live AI watchlist to the shared Redis key the backend serves at
/api/ai-engine/watchlist.

Trading-day rhythm:
  • Pre-open  — a fresh scan runs before the market opens so the watchlist is
                ready for the session (snapshot stored for later grading).
  • Intraday  — periodic re-scans keep it current (and manual /scan works too).
  • Post-close— the morning watchlist is graded against the actual day move to
                produce a *signal score* (how accurate each call was). That
                feedback calibrates the scanner's confidence for future scans,
                so the system keeps learning.
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
_WATCHLIST_KEY   = "ai_engine:watchlist"
_CANDIDATES_KEY  = "ai_engine:scan_candidates"           # candidate pool for the sentiment-service
_SENTIMENT_KEY   = "ai_engine:sentiment:{}"              # per-symbol news signal (sentiment-service)
_PREMARKET_KEY   = "ai_engine:watchlist:premarket"   # snapshot graded after close
_CALIBRATION_KEY = "ai_engine:scan_calibration"       # learned confidence multipliers
_EVAL_KEY        = "ai_engine:scan_eval:latest"       # last post-market grade

# Intraday-fitness gates — a stock must clear these to be tradable intraday
MIN_AVG_VOLUME = float(os.getenv("SCAN_MIN_VOLUME", "300000"))   # liquidity
MIN_ATR_PCT    = float(os.getenv("SCAN_MIN_ATR_PCT", "1.2"))     # daily true range %
MIN_PRICE      = float(os.getenv("SCAN_MIN_PRICE", "30"))        # avoid illiquid penny stocks
TOP_N          = int(os.getenv("SCAN_TOP_N", "15"))
CANDIDATE_POOL_N = int(os.getenv("SCAN_CANDIDATE_POOL", "30"))   # names the sentiment-service covers
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", str(20 * 60)))   # intraday sweep cadence
FETCH_DELAY    = float(os.getenv("SCAN_FETCH_DELAY", "0.25"))    # be gentle on Yahoo

# Trading-day schedule (IST, minutes past midnight)
MARKET_OPEN_MIN  = int(os.getenv("SCAN_MARKET_OPEN_MIN", str(9 * 60 + 15)))    # 09:15
MARKET_CLOSE_MIN = int(os.getenv("SCAN_MARKET_CLOSE_MIN", str(15 * 60 + 30)))  # 15:30
PREMARKET_MIN    = int(os.getenv("SCAN_PREMARKET_MIN", str(9 * 60)))           # 09:00 pre-open scan
POSTMARKET_MIN   = int(os.getenv("SCAN_POSTMARKET_MIN", str(15 * 60 + 40)))    # 15:40 grade

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

_redis: redis.Redis | None = None
_state = {
    "last_scan": None, "scanned": 0, "candidates": 0, "running": False,
    "last_premarket_date": None, "last_eval_date": None,
    "last_eval": None, "calibration": None, "market_regime": "neutral",
}


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


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _macd(closes: list[float]) -> tuple[float, float, float]:
    """Return (macd_line, signal_line, histogram) for the latest bar."""
    if len(closes) < 35:
        return 0.0, 0.0, 0.0
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema_series(macd_line, 9)
    return macd_line[-1], signal[-1], macd_line[-1] - signal[-1]


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def _analyze(candles: list[dict], regime: int = 0, calib: dict | None = None) -> dict | None:
    if len(candles) < 35:
        return None
    opens  = [c["o"] for c in candles]
    closes = [c["c"] for c in candles]
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    vols   = [c["v"] for c in candles]
    price  = closes[-1]
    if price <= 0:
        return None

    # ── Core tradability ──
    avg_vol = sum(vols[-20:]) / min(20, len(vols))
    atr     = sum(highs[i] - lows[i] for i in range(len(closes) - 14, len(closes))) / 14
    atr_pct = atr / price * 100
    range_pct = sum((highs[i] - lows[i]) / closes[i] for i in range(len(closes) - 14, len(closes))) / 14 * 100
    rel_vol = vols[-1] / avg_vol if avg_vol else 1.0

    # ── Trend / momentum / structure ──
    rsi   = _rsi(closes)
    mom   = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0.0
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma_trend = 1 if sma20 > sma50 else -1
    _, _, macd_hist = _macd(closes)
    gap_pct = (opens[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 and closes[-2] else 0.0
    hi20 = max(highs[-20:]); lo20 = min(lows[-20:])
    dist_from_high = (hi20 - price) / price * 100 if price else 0.0   # room to the upside
    dist_from_low  = (price - lo20) / price * 100 if price else 0.0

    fit = (avg_vol >= MIN_AVG_VOLUME) and (atr_pct >= MIN_ATR_PCT) and (price >= MIN_PRICE)

    # ── Directional vote over the indicators that move price ──
    net = 0.0
    if price > sma20:      net += 1
    else:                  net -= 1
    net += sma_trend                                    # SMA20 vs SMA50 regime
    if macd_hist > 0:      net += 1
    elif macd_hist < 0:    net -= 1
    if mom > 1.0:          net += 1
    elif mom < -1.0:       net -= 1
    if rsi < 35:           net += 1                     # oversold bounce
    elif rsi > 68:         net -= 1                     # overbought fade
    if rel_vol > 1.3 and mom > 0:   net += 1            # accumulation
    elif rel_vol > 1.3 and mom < 0: net -= 1            # distribution
    net += 0.5 * regime                                 # align with the broader market
    max_net = 6.5
    conviction = min(1.0, abs(net) / max_net)

    if net >= 1.5:
        action = "BUY"
    elif net <= -1.5:
        action = "SELL"
    else:
        action = "HOLD"

    # ── Intraday-suitability score: tradability + directional conviction ──
    # Volatility (ATR) is weighted highest: only stocks that actually move enough
    # intraday can clear transaction costs, so we fish where the big moves are.
    liq_score = min(1.0, avg_vol / 3_000_000)
    vol_score = max(0.0, min(1.0, (atr_pct - MIN_ATR_PCT) / 3.0 + 0.3))
    relvol_score = min(1.0, rel_vol / 2.0)
    tradability = liq_score * 0.35 + vol_score * 0.50 + relvol_score * 0.15
    raw_score = round((tradability * 0.55 + conviction * 0.45), 4)

    # Learned calibration: scale confidence by how accurate this action has been
    mult = 1.0
    if calib:
        mult = float(calib.get(action, calib.get("overall_mult", 1.0)) or 1.0)
    confidence = round(min(0.98, max(0.30, (0.40 + 0.50 * conviction) * mult)), 3)
    signal_score = round(min(100.0, (0.5 * tradability + 0.5 * conviction) * 100 * mult), 1)

    regime_txt = {1: "bullish", -1: "bearish", 0: "neutral"}[regime]
    reasoning = (f"Liquidity {avg_vol/1e6:.1f}M/day ({rel_vol:.1f}× avg), volatility {atr_pct:.1f}% ATR, "
                 f"trend {'up' if sma_trend > 0 else 'down'} (SMA20{'>' if sma20 > sma50 else '<'}SMA50), "
                 f"MACD {'+' if macd_hist >= 0 else '−'}, RSI {rsi:.0f}, momentum {mom:+.1f}%, "
                 f"gap {gap_pct:+.1f}%, market {regime_txt} — "
                 + ("strong intraday fit" if fit else "below intraday thresholds"))

    return {
        "price": round(price, 2),
        "action": action,
        "confidence": confidence,
        "agreement": round(conviction, 3),
        "score": raw_score,
        "signal_score": signal_score,
        "intraday_fit": fit,
        "reasoning": reasoning,
        "metrics": {
            "avg_volume": int(avg_vol),
            "rel_volume": round(rel_vol, 2),
            "atr_pct": round(atr_pct, 2),
            "range_pct": round(range_pct, 2),
            "rsi": round(rsi, 1),
            "momentum_pct": round(mom, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "sma_trend": "up" if sma_trend > 0 else "down",
            "macd_hist": round(macd_hist, 3),
            "gap_pct": round(gap_pct, 2),
            "dist_from_high_pct": round(dist_from_high, 2),
            "dist_from_low_pct": round(dist_from_low, 2),
            "market_regime": regime_txt,
            "liquidity_score": round(liq_score, 3),
            "volatility_score": round(vol_score, 3),
        },
    }


# ── Data fetch ────────────────────────────────────────────────────────────────

async def _fetch_chart(client: httpx.AsyncClient, ysym: str) -> list[dict]:
    p2 = int(time.time())
    p1 = p2 - 140 * 86400
    try:
        r = await client.get(_YAHOO + ysym,
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
        logger.debug("fetch %s failed: %s", ysym, exc)
        return []


async def _fetch_daily(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    return await _fetch_chart(client, f"{symbol}.NS")


async def _market_regime(client: httpx.AsyncClient) -> int:
    """+1 bullish / -1 bearish / 0 neutral, from NIFTY 50 trend (SMA20 vs SMA50 + momentum)."""
    candles = await _fetch_chart(client, "%5ENSEI")  # ^NSEI
    if len(candles) < 50:
        return 0
    closes = [c["c"] for c in candles]
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    mom = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0.0
    if sma20 > sma50 and mom > 0:
        return 1
    if sma20 < sma50 and mom < 0:
        return -1
    return 0


# ── Calibration (learning loop) ───────────────────────────────────────────────

async def _load_calibration() -> dict:
    try:
        r = await _get_redis()
        raw = await r.get(_CALIBRATION_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"BUY": 1.0, "SELL": 1.0, "HOLD": 1.0, "overall_mult": 1.0, "accuracy": None, "samples": 0}


# ── Core scan ─────────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


async def scan_once(phase: str = "intraday") -> dict:
    """Sweep the whole universe once, keep intraday-fit names, store the watchlist.

    phase: "premarket" also stores a dated snapshot that gets graded after close.
    """
    _state["running"] = True
    calib = await _load_calibration()
    candidates: list[dict] = []
    scanned = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        regime = await _market_regime(client)
        _state["market_regime"] = {1: "bullish", -1: "bearish", 0: "neutral"}[regime]
        for sym, name in UNIVERSE.items():
            candles = await _fetch_daily(client, sym)
            scanned += 1
            res = _analyze(candles, regime=regime, calib=calib)
            if res and res["intraday_fit"]:
                candidates.append({"symbol": sym, "name": name, "source": "scanner", **res})
            await asyncio.sleep(FETCH_DELAY)

    # ── News-catalyst boost ───────────────────────────────────────────────────
    # Pull the LLM news signal (written by the sentiment-service) for each
    # candidate and let a fresh, high-conviction, directional catalyst lift the
    # ranking — so high-ATR names *with* a real news catalyst float to the top
    # (that's where moves big enough to clear costs happen). Long-only, so
    # positive news boosts and negative news is penalised.
    r = await _get_redis()
    for c in candidates:
        boost = 0.0
        try:
            raw_s = await r.get(_SENTIMENT_KEY.format(c["symbol"]))
            if raw_s:
                nd = json.loads(raw_s)
                if int(nd.get("headlines_count", 0)) > 0 and float(nd.get("confidence", 0) or 0) >= 0.6:
                    score = float(nd.get("score", 0) or 0)        # -1..1
                    conf  = float(nd.get("confidence", 0) or 0)
                    boost = max(-0.30, min(0.50, score * conf * 0.6))
                    c["catalyst"] = nd.get("catalyst") or nd.get("summary")
                    c["news_sentiment"] = nd.get("sentiment")
        except Exception:
            pass
        c["catalyst_boost"] = round(boost, 3)
        c["rank_score"] = round(c["signal_score"] * (1 + boost), 2)

    candidates.sort(key=lambda r: (r["action"] != "BUY", -r["rank_score"]))
    watchlist = candidates[:TOP_N]

    # Publish the candidate pool so the sentiment-service analyses the names just
    # below the cut too — that's how a fresh catalyst can pull a stock *into* the
    # watchlist next cycle (otherwise news would only ever reinforce incumbents).
    try:
        pool = [{"symbol": c["symbol"], "name": c["name"]} for c in candidates[:CANDIDATE_POOL_N]]
        await r.set(_CANDIDATES_KEY, json.dumps({"updated_at": _ist_now().isoformat(), "items": pool}), ex=86400)
    except Exception as exc:
        logger.debug("candidate pool write failed: %s", exc)

    now = _ist_now()
    payload = {
        "updated_at": now.isoformat(),
        "phase": phase,
        "scanned": scanned,
        "universe": len(UNIVERSE),
        "candidates": len(candidates),
        "market_regime": _state["market_regime"],
        "calibration": {"accuracy": calib.get("accuracy"), "samples": calib.get("samples", 0)},
        "items": watchlist,
    }
    try:
        r = await _get_redis()
        await r.set(_WATCHLIST_KEY, json.dumps(payload), ex=86400)
        if phase == "premarket":
            await r.set(f"{_PREMARKET_KEY}:{now.strftime('%Y-%m-%d')}", json.dumps(payload), ex=86400 * 3)
    except Exception as exc:
        logger.warning("watchlist write failed: %s", exc)

    _state.update({"last_scan": payload["updated_at"], "scanned": scanned,
                   "candidates": len(candidates), "running": False, "calibration": payload["calibration"]})
    logger.info("scan(%s) complete: %d scanned, %d intraday-fit, %d on watchlist, market %s",
                phase, scanned, len(candidates), len(watchlist), _state["market_regime"])
    return payload


# ── Post-market evaluation (signal score + learning) ──────────────────────────

async def evaluate_day(date_str: str | None = None) -> dict:
    """Grade the morning watchlist against the actual day move → signal scores.

    For each pre-market pick we compare the predicted action with how the stock
    actually moved during the session, producing a per-stock accuracy and an
    aggregate signal score. The result is stored, fed back to the backend's
    learning loop, and used to calibrate future confidence.
    """
    now = _ist_now()
    date_str = date_str or now.strftime("%Y-%m-%d")
    r = await _get_redis()
    raw = await r.get(f"{_PREMARKET_KEY}:{date_str}") or await r.get(_WATCHLIST_KEY)
    if not raw:
        return {"status": "no_watchlist", "date": date_str}
    snapshot = json.loads(raw)
    items = snapshot.get("items", [])
    if not items:
        return {"status": "empty", "date": date_str}

    graded: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for w in items:
            candles = await _fetch_daily(client, w["symbol"])
            await asyncio.sleep(FETCH_DELAY)
            if not candles:
                continue
            last = candles[-1]
            day_ret = (last["c"] - last["o"]) / last["o"] * 100 if last["o"] else 0.0
            action = w.get("action", "HOLD")
            # realised return *in the predicted direction* (positive = good call)
            if action == "BUY":
                realized = day_ret
                correct = day_ret >= 0.3
            elif action == "SELL":
                realized = -day_ret
                correct = day_ret <= -0.3
            else:  # HOLD
                realized = -abs(day_ret)
                correct = abs(day_ret) < 0.5
            graded.append({
                "symbol": w["symbol"], "action": action,
                "predicted_confidence": w.get("confidence"),
                "predicted_signal_score": w.get("signal_score"),
                "day_return_pct": round(day_ret, 2),
                "realized_return_pct": round(realized, 2),
                "correct": bool(correct),
            })

    if not graded:
        return {"status": "no_data", "date": date_str}

    hits = sum(1 for g in graded if g["correct"])
    accuracy = round(hits / len(graded), 4)
    avg_realized = round(sum(g["realized_return_pct"] for g in graded) / len(graded), 2)
    by_action: dict[str, dict] = {}
    for g in graded:
        a = by_action.setdefault(g["action"], {"n": 0, "hits": 0})
        a["n"] += 1; a["hits"] += 1 if g["correct"] else 0

    eval_payload = {
        "date": date_str, "evaluated_at": now.isoformat(),
        "picks": len(graded), "hits": hits,
        "accuracy": accuracy, "avg_realized_return_pct": avg_realized,
        "by_action": {k: {"n": v["n"], "accuracy": round(v["hits"] / v["n"], 4)} for k, v in by_action.items()},
        "results": sorted(graded, key=lambda g: -g["realized_return_pct"]),
    }
    try:
        await r.set(_EVAL_KEY, json.dumps(eval_payload), ex=86400 * 30)
        await r.set(f"ai_engine:scan_eval:{date_str}", json.dumps(eval_payload), ex=86400 * 90)
    except Exception as exc:
        logger.warning("eval write failed: %s", exc)

    await _update_calibration(by_action, accuracy)
    await _push_feedback(eval_payload)

    _state.update({"last_eval_date": date_str, "last_eval": {
        "date": date_str, "accuracy": accuracy, "picks": len(graded),
        "avg_realized_return_pct": avg_realized}})
    logger.info("post-market grade %s: %d picks, accuracy %.0f%%, avg realised %+.2f%%",
                date_str, len(graded), accuracy * 100, avg_realized)
    return eval_payload


async def _update_calibration(by_action: dict, accuracy: float) -> None:
    """EMA-blend today's accuracy into per-action confidence multipliers so the
    next scans trust historically-accurate signals more and shaky ones less."""
    calib = await _load_calibration()
    alpha = 0.3  # weight on the newest day
    def _mult(acc: float) -> float:
        return round(max(0.7, min(1.3, 0.7 + 0.6 * acc)), 3)
    for action in ("BUY", "SELL", "HOLD"):
        if action in by_action:
            acc = by_action[action]["hits"] / by_action[action]["n"]
            prev = float(calib.get(action, 1.0) or 1.0)
            calib[action] = round(prev * (1 - alpha) + _mult(acc) * alpha, 3)
    prev_overall = float(calib.get("overall_mult", 1.0) or 1.0)
    calib["overall_mult"] = round(prev_overall * (1 - alpha) + _mult(accuracy) * alpha, 3)
    prev_acc = calib.get("accuracy")
    calib["accuracy"] = round(accuracy if prev_acc is None else prev_acc * 0.7 + accuracy * 0.3, 4)
    calib["samples"] = int(calib.get("samples", 0)) + 1
    calib["updated_at"] = _ist_now().isoformat()
    try:
        r = await _get_redis()
        await r.set(_CALIBRATION_KEY, json.dumps(calib), ex=86400 * 120)
        _state["calibration"] = {"accuracy": calib["accuracy"], "samples": calib["samples"]}
    except Exception as exc:
        logger.warning("calibration write failed: %s", exc)


async def _push_feedback(eval_payload: dict) -> None:
    """Hand the graded results to the backend so they feed the system's learning."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{BACKEND_URL}/api/ai-engine/scan-feedback", json=eval_payload)
    except Exception as exc:
        logger.debug("scan feedback push skipped: %s", exc)


# ── Schedulers ────────────────────────────────────────────────────────────────

async def scanner_loop() -> None:
    """Continuous intraday sweep — keeps the watchlist fresh during the day."""
    await asyncio.sleep(5)
    while True:
        try:
            await scan_once(phase="intraday")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("scan loop error: %s", exc)
        await asyncio.sleep(SCAN_INTERVAL)


async def schedule_loop() -> None:
    """Trading-day rhythm: pre-open scan once before the open, post-close grade
    once after the close. Checks every minute; idempotent per day."""
    await asyncio.sleep(15)
    while True:
        try:
            now = _ist_now()
            today = now.strftime("%Y-%m-%d")
            minutes = now.hour * 60 + now.minute
            weekday = now.weekday() < 5
            if weekday and PREMARKET_MIN <= minutes < MARKET_OPEN_MIN and _state["last_premarket_date"] != today:
                _state["last_premarket_date"] = today
                logger.info("pre-open scan for %s", today)
                await scan_once(phase="premarket")
            if weekday and minutes >= POSTMARKET_MIN and _state["last_eval_date"] != today:
                logger.info("post-close evaluation for %s", today)
                await evaluate_day(today)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("schedule loop error: %s", exc)
        await asyncio.sleep(60)


async def warm_state() -> None:
    """Load last eval + calibration into state on boot so the UI has them."""
    try:
        r = await _get_redis()
        ev = await r.get(_EVAL_KEY)
        if ev:
            e = json.loads(ev)
            _state["last_eval"] = {"date": e.get("date"), "accuracy": e.get("accuracy"),
                                   "picks": e.get("picks"), "avg_realized_return_pct": e.get("avg_realized_return_pct")}
            _state["last_eval_date"] = e.get("date")
        cal = await r.get(_CALIBRATION_KEY)
        if cal:
            c = json.loads(cal)
            _state["calibration"] = {"accuracy": c.get("accuracy"), "samples": c.get("samples", 0)}
    except Exception as exc:
        logger.debug("warm_state skipped: %s", exc)


async def get_latest_eval() -> dict | None:
    try:
        r = await _get_redis()
        raw = await r.get(_EVAL_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def get_state() -> dict:
    return dict(_state)
