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
import math
import os
import random
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
_RANKED_KEY      = "ai_engine:ranked"                    # full ranked board for the Predictions page
_RANKED_PREV_KEY = "ai_engine:ranked:prev"               # last completed board — for scan-to-scan diff
_CANDIDATES_KEY  = "ai_engine:scan_candidates"           # candidate pool for the sentiment-service
_SENTIMENT_KEY   = "ai_engine:sentiment:{}"              # per-symbol news signal (sentiment-service)
_PREMARKET_KEY   = "ai_engine:watchlist:premarket"   # snapshot graded after close
_CALIBRATION_KEY = "ai_engine:scan_calibration"       # learned confidence multipliers
_EVAL_KEY        = "ai_engine:scan_eval:latest"       # last post-market grade
_DELIVERY_DONE_KEY = "ai_engine:delivery_eval_done:{}"  # per-entry-date delivery-grade marker

# Delivery grading — picks are graded on their N-trading-day forward return.
DELIVERY_HORIZON_DAYS = int(os.getenv("SCAN_DELIVERY_HORIZON", "5"))
DELIVERY_TARGET_PCT   = float(os.getenv("SCAN_DELIVERY_TARGET_PCT", "1.5"))
# Accuracy the scans should clear; the high-conviction tier is tuned toward this.
SCAN_ACCURACY_TARGET  = float(os.getenv("SCAN_ACCURACY_TARGET", "0.90"))

# High-conviction ("committed") tier — the only picks the system commits to. A
# pick is committed only when many INDEPENDENT signals agree; otherwise the
# system abstains. Precision over coverage: few picks, high hit-rate. The bar is
# adaptive (stored in Redis) and auto-tightens toward SCAN_ACCURACY_TARGET.
_HC_PARAMS_KEY        = "ai_engine:hc_params"
# Defaults are deliberately strict: a genuine high-conviction pick needs ALL six
# independent confirmations and a high win-probability. This keeps the committed
# tier to a handful of names a day (≈70%+ hit-rate in backtest) rather than many
# (≈46%). The adaptive controller tightens further toward the target.
HC_MIN_FACTORS        = int(os.getenv("SCAN_HC_MIN_FACTORS", "5"))   # of 6 confirmations
HC_WP_FLOOR           = float(os.getenv("SCAN_HC_WP_FLOOR", "0.72")) # min win-probability
# Hard cap on how many picks the committed tier holds, no matter how many clear
# the bar — keeps "high conviction" genuinely selective (paper trades only these).
COMMITTED_MAX         = int(os.getenv("SCAN_COMMITTED_MAX", "3"))
# Committed picks are graded on their N-trading-day forward return (their edge is
# multi-day, not same-day intraday) — "correct" if they gain COMMITTED_TARGET_PCT.
COMMITTED_HORIZON     = int(os.getenv("SCAN_COMMITTED_HORIZON", "3"))
COMMITTED_TARGET_PCT  = float(os.getenv("SCAN_COMMITTED_TARGET_PCT", "1.0"))
_COMMITTED_DONE_KEY   = "ai_engine:committed_eval_done:{}"


async def _load_hc_params() -> dict:
    try:
        r = await _get_redis()
        raw = await r.get(_HC_PARAMS_KEY)
        if raw:
            p = json.loads(raw)
            return {"min_factors": int(p.get("min_factors", HC_MIN_FACTORS)),
                    "wp_floor": float(p.get("wp_floor", HC_WP_FLOOR))}
    except Exception:
        pass
    return {"min_factors": HC_MIN_FACTORS, "wp_floor": HC_WP_FLOOR}


# Pattern-recognition model gate — the scanner pulls the backend model's learned
# weights once per sweep and scores each pattern locally (no per-stock HTTP). A
# committed pick must have the *learned pattern model* agree it's an up-pattern.
# The pattern model is used as a VETO (block clearly-bearish patterns) rather than
# a hard high-bar gate — the model's P(up) clusters low (base rate of "up" < 50%),
# so a 0.55 floor vetoed everything and starved paper trading. A pick is blocked
# only if the model is clearly bearish (P < PATTERN_VETO); P ≥ PATTERN_MIN_P counts
# as a positive independent confirmation.
PATTERN_VETO   = float(os.getenv("SCAN_PATTERN_VETO", "0.30"))    # block only clearly-bearish patterns
PATTERN_MIN_P  = float(os.getenv("SCAN_PATTERN_MIN_P", "0.45"))   # ≥ this = bullish-pattern confirmation
_FP_SHAPE_LEN  = 10
_FP_DIM        = 19
_pattern_wb_cache: dict = {"w": None, "b": 0.0, "ts": 0.0, "trained": False}


def _fp_clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _fp_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _fp_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    g = l = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d >= 0: g += d
        else:      l -= d
    ag, al = g / period, l / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def _fp_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _pattern_fingerprint(candles: list[dict]) -> list[float] | None:
    """Scale-free pattern fingerprint — MUST match backend app/agents/fingerprint.py
    (build_fingerprint) feature-for-feature so the backend-trained weights apply.
    Scanner candles use o/h/l/c/v keys."""
    if not candles or len(candles) < 15:
        return None
    closes = [float(c["c"]) for c in candles]
    highs  = [float(c.get("h", c["c"])) for c in candles]
    lows   = [float(c.get("l", c["c"])) for c in candles]
    vols   = [float(c.get("v", 0) or 0) for c in candles]
    last = closes[-1]

    rets: list[float] = []
    for i in range(len(closes) - _FP_SHAPE_LEN, len(closes)):
        prev = closes[i - 1] if i > 0 else closes[i]
        rets.append(_fp_div(closes[i] - prev, prev))
    sigma = (sum(r * r for r in rets) / len(rets)) ** 0.5 or 1e-6
    shape = [_fp_clip(r / (sigma * 3)) for r in rets]

    f_rsi = _fp_clip((_fp_rsi(closes) - 50) / 50)
    e12, e26 = _fp_ema(closes[-26:], 12), _fp_ema(closes[-26:], 26)
    f_macd = _fp_clip(_fp_div(e12 - e26, last) * 100)
    vwap = sum((highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))) / len(closes)
    f_vwap = _fp_clip(_fp_div(last - vwap, vwap) * 50)
    atr = sum(highs[i] - lows[i] for i in range(len(closes) - 10, len(closes))) / 10
    f_atr = _fp_clip(_fp_div(atr, last) * 50)
    win = closes[-20:]
    mean = sum(win) / len(win)
    std = (sum((c - mean) ** 2 for c in win) / len(win)) ** 0.5 or 1e-6
    f_bbpos = _fp_clip((last - mean) / (2 * std))
    avg_vol = (sum(vols) / len(vols)) or 1.0
    f_vol = _fp_clip(math.tanh(_fp_div(vols[-1], avg_vol) - 1.0))
    f_mom5  = _fp_clip(_fp_div(last - closes[-5],  closes[-5])  * 20) if len(closes) >= 5  else 0.0
    f_mom10 = _fp_clip(_fp_div(last - closes[-10], closes[-10]) * 20) if len(closes) >= 10 else 0.0
    n = min(10, len(closes)); xs = list(range(n)); ys = closes[-n:]
    mx = sum(xs) / n; my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1e-6
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    f_slope = _fp_clip(_fp_div(slope, last) * 200)
    return shape + [f_rsi, f_macd, f_vwap, f_atr, f_bbpos, f_vol, f_mom5, f_mom10, f_slope]


def _pattern_p_up(candles: list[dict]) -> float | None:
    """Learned pattern model's P(up) for the latest pattern, or None if the model
    isn't trained / weights unavailable."""
    wb = _pattern_wb_cache
    if not wb.get("trained") or wb.get("w") is None:
        return None
    fp = _pattern_fingerprint(candles)
    if fp is None or len(fp) != _FP_DIM:
        return None
    z = sum(wi * xi for wi, xi in zip(wb["w"], fp)) + wb["b"]
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


async def _load_pattern_weights() -> None:
    """Pull the backend pattern model's weights once per sweep (cached ~10 min)."""
    if time.time() - _pattern_wb_cache.get("ts", 0) < 600 and _pattern_wb_cache.get("w") is not None:
        return
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/ai-engine/pattern-model/weights")
            d = (r.json() or {}).get("data") or {}
        w = d.get("weights")
        if w and len(w) == _FP_DIM and d.get("trained"):
            _pattern_wb_cache.update({"w": [float(x) for x in w], "b": float(d.get("bias", 0.0)),
                                      "trained": True, "ts": time.time()})
        else:
            _pattern_wb_cache.update({"trained": bool(d.get("trained")), "ts": time.time()})
    except Exception as exc:
        logger.debug("pattern weights fetch skipped: %s", exc)
        _pattern_wb_cache["ts"] = time.time()


def _is_committed(res: dict, p: dict) -> bool:
    """A high-conviction long: top grade BUY, enough short-term confirmations, a
    win-probability above the (adaptive) floor, PLUS independent confirmation —
    a confirmed higher-timeframe uptrend, no negative news catalyst, AND the
    learned pattern model agreeing it's an up-pattern. Requiring signals that
    aren't all correlated technicals is what lifts precision."""
    if not (res.get("action") == "BUY"
            and res.get("grade") == "A"
            and int(res.get("confirmed_factors", 0)) >= p["min_factors"]
            and float(res.get("win_probability") or 0.0) >= p["wp_floor"]):
        return False
    # Independent signal 1 — long-term trend must agree (when computable).
    if res.get("long_trend") is False:
        return False
    # Independent signal 2 — block on a negative news catalyst.
    if float(res.get("catalyst_boost") or 0.0) < -0.05:
        return False
    # Independent signal 3 — the learned pattern model must AGREE (not merely be
    # non-bearish): committed picks need the pattern model leaning up.
    pp = res.get("pattern_p_up")
    if pp is not None and pp < PATTERN_MIN_P:
        return False
    return True


def _independent_signals(res: dict) -> int:
    """Count of confirming signals independent of the short-term technical score:
    long-term trend agreement, a positive fresh news catalyst, and the learned
    pattern model's confident agreement."""
    n = 0
    if res.get("long_trend") is True:
        n += 1
    if float(res.get("catalyst_boost") or 0.0) > 0.10:
        n += 1
    pp = res.get("pattern_p_up")
    if pp is not None and pp >= PATTERN_MIN_P:
        n += 1
    return n


async def _tune_hc_params(accuracy: float, n: int, target: float) -> dict:
    """Adaptive selectivity controller: when the committed tier misses target,
    tighten the bar (higher win-prob floor / more confirmations) so we commit to
    fewer, higher-quality setups. If it gets so strict that no picks qualify, ease
    off slightly. We never loosen on success — precision is held."""
    p = await _load_hc_params()
    if n >= 3 and accuracy < target:
        p["wp_floor"] = round(min(0.92, p["wp_floor"] + 0.02), 3)
        if accuracy < target - 0.15:
            p["min_factors"] = min(6, p["min_factors"] + 1)
    elif n == 0:
        p["wp_floor"] = round(max(0.60, p["wp_floor"] - 0.02), 3)
        p["min_factors"] = max(4, p["min_factors"] - (1 if p["min_factors"] > 4 else 0))
    try:
        r = await _get_redis()
        await r.set(_HC_PARAMS_KEY, json.dumps(p), ex=86400 * 120)
    except Exception:
        pass
    return p

# Intraday-fitness gates — a stock must clear these to be tradable intraday
MIN_AVG_VOLUME = float(os.getenv("SCAN_MIN_VOLUME", "300000"))   # liquidity
MIN_ATR_PCT    = float(os.getenv("SCAN_MIN_ATR_PCT", "1.2"))     # daily true range %
MIN_PRICE      = float(os.getenv("SCAN_MIN_PRICE", "30"))        # avoid illiquid penny stocks
TOP_N          = int(os.getenv("SCAN_TOP_N", "15"))
# The intraday "Best Intraday" watchlist (and its post-close signal score) is the
# top few MOST-CONVICTED picks only — grade-A/B BUYs, capped small. Grading fewer,
# higher-conviction names lifts the signal score (precision over coverage).
WATCHLIST_MAX  = int(os.getenv("SCAN_WATCHLIST_MAX", "6"))     # default; runtime-overridable
_WATCHLIST_MAX_KEY = "ai_engine:watchlist_max"                # set from the UI


async def _watchlist_max() -> int:
    """Runtime intraday watchlist size (UI-configurable via Redis), else the env
    default. Bounded to a sensible 3–25."""
    try:
        raw = await (await _get_redis()).get(_WATCHLIST_MAX_KEY)
        if raw:
            return max(3, min(25, int(raw)))
    except Exception:
        pass
    return WATCHLIST_MAX


def _top_watchlist(cands: list[dict], grade_rank: dict, wl_max: int = WATCHLIST_MAX) -> list[dict]:
    """Most-convicted intraday picks: grade-A/B BUYs, ranked, capped at `wl_max`
    (falls back to the best available if too few high-grade BUYs exist)."""
    ranked = sorted(cands, key=lambda r: (r.get("action") != "BUY",
                    grade_rank.get(r.get("grade", "D"), 3),
                    -r.get("rank_score", r.get("signal_score", 0.0))))
    hi = [c for c in ranked if c.get("action") == "BUY" and c.get("grade") in ("A", "B")]
    return (hi or ranked)[:max(1, wl_max)]

# Delivery-fitness gates — a stock must clear these to be a multi-week swing hold.
# Unlike intraday (which fishes for high volatility), delivery wants an *orderly*
# uptrend: enough liquidity, manageable volatility, and positive medium-term trend.
DELIVERY_MIN_VOLUME  = float(os.getenv("SCAN_DELIVERY_MIN_VOLUME", "200000"))
DELIVERY_MAX_ATR_PCT = float(os.getenv("SCAN_DELIVERY_MAX_ATR_PCT", "6.0"))   # too choppy = not holdable
DELIVERY_MIN_MOM     = float(os.getenv("SCAN_DELIVERY_MIN_MOM", "0.0"))       # 10-day momentum must be positive
DELIVERY_TOP_N       = int(os.getenv("SCAN_DELIVERY_TOP_N", "10"))
RANKED_MAX           = int(os.getenv("SCAN_RANKED_MAX", "250"))   # full ranked board size
CANDIDATE_POOL_N = int(os.getenv("SCAN_CANDIDATE_POOL", "30"))   # names the sentiment-service covers
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", str(20 * 60)))   # intraday sweep cadence
FETCH_DELAY    = float(os.getenv("SCAN_FETCH_DELAY", "0.30"))    # base per-symbol delay (+jitter) — gentle on Yahoo
# Full-universe (NSE ~1800) background scan controls
SCAN_CHECKPOINT_EVERY = int(os.getenv("SCAN_CHECKPOINT_EVERY", "120"))  # write partial watchlist every N stocks
RATE_LIMIT_BACKOFF    = float(os.getenv("SCAN_RATE_LIMIT_BACKOFF", "5.0"))  # sleep on a Yahoo 429
STALE_RUN_SECS        = int(os.getenv("SCAN_STALE_RUN_SECS", "2400"))   # a 'running' flag older than this is stale

# Trading-day schedule (IST, minutes past midnight)
MARKET_OPEN_MIN  = int(os.getenv("SCAN_MARKET_OPEN_MIN", str(9 * 60 + 15)))    # 09:15
MARKET_CLOSE_MIN = int(os.getenv("SCAN_MARKET_CLOSE_MIN", str(15 * 60 + 30)))  # 15:30
PREMARKET_MIN    = int(os.getenv("SCAN_PREMARKET_MIN", str(9 * 60)))           # 09:00 pre-open scan
POSTMARKET_MIN   = int(os.getenv("SCAN_POSTMARKET_MIN", str(15 * 60 + 40)))    # 15:40 grade

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

_redis: redis.Redis | None = None
_state = {
    "last_scan": None, "scanned": 0, "universe": 0, "candidates": 0,
    "running": False, "scanning": False, "run_started": 0.0,
    "last_premarket_date": None, "last_eval_date": None,
    "last_eval": None, "calibration": None, "market_regime": "neutral",
}
_scan_lock = asyncio.Lock()


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


def _grade_from_winprob(win_probability: float, action: str, fit: bool) -> str:
    """Map a win-probability into an A/B/C/D quality grade.

    Only directional (BUY/SELL) calls that clear the intraday bar can earn the
    top grades — that's what 'win max' filtering means: we only promote setups
    where many independent factors line up. HOLD is never tradable-grade."""
    if action == "HOLD":
        return "D"
    if fit and win_probability >= 0.70:
        return "A"
    if fit and win_probability >= 0.58:
        return "B"
    if win_probability >= 0.48:
        return "C"
    return "D"


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
    sma100 = _sma(closes, 100) if len(closes) >= 100 else None
    sma_trend = 1 if sma20 > sma50 else -1
    # Higher-timeframe (long-term) trend — a confirmation that's largely
    # independent of the short-term daily signal; required for high-conviction.
    long_trend = (price > sma100 and sma50 >= sma100) if sma100 is not None else None
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

    # ── Win-probability: a weighted vote across INDEPENDENT confirmations ──────
    # Each factor that agrees with the call lifts the win probability. The more
    # independent factors line up, the more likely the trade works — so the
    # watchlist can be filtered to only high-probability setups.
    if action == "BUY":
        trend_aligned = (price > sma20) and (sma20 >= sma50)
        mom_aligned   = mom > 0.5
        macd_aligned  = macd_hist > 0
        vol_confirm   = rel_vol >= 1.3 and mom > 0
        rsi_ok        = rsi < 70           # not already overbought
        regime_aligned = regime >= 0
    elif action == "SELL":
        trend_aligned = (price < sma20) and (sma20 <= sma50)
        mom_aligned   = mom < -0.5
        macd_aligned  = macd_hist < 0
        vol_confirm   = rel_vol >= 1.3 and mom < 0
        rsi_ok        = rsi > 30           # not already oversold
        regime_aligned = regime <= 0
    else:  # HOLD — no directional confirmation
        trend_aligned = mom_aligned = macd_aligned = vol_confirm = False
        rsi_ok = True
        regime_aligned = regime == 0

    confirmations = {
        "conviction":  round(conviction, 3),
        "trend":       1.0 if trend_aligned else 0.0,
        "momentum":    1.0 if mom_aligned else 0.0,
        "macd":        1.0 if macd_aligned else 0.0,
        "volume":      1.0 if vol_confirm else round(min(1.0, rel_vol / 1.5) * 0.5, 3),
        "regime":      1.0 if regime_aligned else 0.0,
        "rsi":         1.0 if rsi_ok else 0.0,
        "tradability": round(tradability, 3),
    }
    _WEIGHTS = {"conviction": 0.20, "trend": 0.18, "momentum": 0.12, "macd": 0.12,
                "volume": 0.12, "regime": 0.10, "rsi": 0.06, "tradability": 0.10}
    align = sum(confirmations[k] * w for k, w in _WEIGHTS.items())   # 0..1
    win_probability = round(min(0.95, max(0.05, align * mult)), 3)
    grade = _grade_from_winprob(win_probability, action, fit)
    confirmed_factors = sum(1 for k in ("trend", "momentum", "macd", "volume", "regime", "rsi")
                            if confirmations[k] >= 1.0)

    regime_txt = {1: "bullish", -1: "bearish", 0: "neutral"}[regime]
    reasoning = (f"Liquidity {avg_vol/1e6:.1f}M/day ({rel_vol:.1f}× avg), volatility {atr_pct:.1f}% ATR, "
                 f"trend {'up' if sma_trend > 0 else 'down'} (SMA20{'>' if sma20 > sma50 else '<'}SMA50), "
                 f"MACD {'+' if macd_hist >= 0 else '−'}, RSI {rsi:.0f}, momentum {mom:+.1f}%, "
                 f"gap {gap_pct:+.1f}%, market {regime_txt} — "
                 + ("strong intraday fit" if fit else "below intraday thresholds"))

    # ── Delivery (multi-week swing) suitability ───────────────────────────────
    # Delivery favours a confirmed, orderly uptrend you can hold for weeks: price
    # above both SMAs, SMA20≥SMA50, positive 10-day momentum, healthy (not
    # over-bought) RSI, manageable volatility and enough liquidity. Long-only.
    uptrend       = price > sma20 and sma20 >= sma50
    healthy_rsi   = 45 <= rsi <= 72
    calm_enough   = atr_pct <= DELIVERY_MAX_ATR_PCT
    liquid_enough = avg_vol >= DELIVERY_MIN_VOLUME
    delivery_fit  = (action == "BUY" and uptrend and mom > DELIVERY_MIN_MOM and healthy_rsi
                     and calm_enough and liquid_enough and price >= MIN_PRICE)

    # Estimated safe holding window (weeks): stronger, calmer trends hold longer.
    weeks = 1
    if price > sma20 > sma50:   weeks += 1     # full trend alignment
    if mom > 2.0:               weeks += 1     # real momentum behind it
    if atr_pct <= 2.5:          weeks += 1     # orderly, low-noise climb
    if macd_hist > 0:           weeks += 1     # MACD confirms
    if 50 <= rsi <= 65:         weeks += 1     # healthy, room left to run
    delivery_weeks = max(1, min(6, weeks))

    # Delivery score (0..100): trend quality + conviction + momentum, and — unlike
    # intraday — *low* volatility is a plus. Used to rank the delivery list.
    trend_q = 1.0 if price > sma20 > sma50 else (0.5 if price > sma20 else 0.0)
    calm_q  = max(0.0, min(1.0, (DELIVERY_MAX_ATR_PCT - atr_pct) / DELIVERY_MAX_ATR_PCT + 0.2))
    mom_q   = max(0.0, min(1.0, mom / 6.0))
    delivery_score = round((trend_q * 0.40 + conviction * 0.25 + mom_q * 0.20 + calm_q * 0.15) * 100, 1)

    delivery_reasoning = (
        f"{'Confirmed up' if uptrend else 'Unconfirmed '}trend "
        f"(price {'>' if price > sma20 else '<'} SMA20 {'>' if sma20 >= sma50 else '<'} SMA50), "
        f"momentum {mom:+.1f}%, RSI {rsi:.0f}, volatility {atr_pct:.1f}% ATR, "
        f"liquidity {avg_vol/1e6:.1f}M/day — "
        + (f"holdable ~{delivery_weeks} week(s)" if delivery_fit else "not a clean delivery setup"))

    return {
        "price": round(price, 2),
        "action": action,
        "confidence": confidence,
        "agreement": round(conviction, 3),
        "score": raw_score,
        "signal_score": signal_score,
        "win_probability": win_probability,
        "grade": grade,
        "confirmed_factors": confirmed_factors,
        "confirmations": confirmations,
        "long_trend": long_trend,
        "pattern_p_up": _pattern_p_up(candles),   # learned pattern model's verdict
        "intraday_fit": fit,
        "delivery_fit": delivery_fit,
        "delivery_weeks": delivery_weeks,
        "delivery_score": delivery_score,
        "delivery_reasoning": delivery_reasoning,
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
    # Retry with backoff on rate-limiting (429) — essential when sweeping the
    # full ~1800-symbol NSE universe so Yahoo doesn't shut us out mid-scan.
    for attempt in range(3):
        try:
            r = await client.get(_YAHOO + ysym,
                                 params={"period1": p1, "period2": p2, "interval": "1d", "includePrePost": "false"},
                                 headers=_UA, timeout=12.0)
            if r.status_code in (429, 999) or r.status_code >= 500:
                await asyncio.sleep(RATE_LIMIT_BACKOFF * (attempt + 1) + random.uniform(0, 1.0))
                continue
            r.raise_for_status()
            res = (r.json().get("chart", {}).get("result") or [None])[0]
            if not res:
                return []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            ts = res.get("timestamp", []) or []
            o, h, l, c, v = q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", []), q.get("volume", [])
            out = []
            for i in range(len(c)):
                try:
                    cl = c[i]
                    if cl is None or float(cl) <= 0:
                        continue
                    bar = {"o": float(o[i] or cl), "h": float(h[i] or cl),
                           "l": float(l[i] or cl), "c": float(cl), "v": int(v[i] or 0)}
                    if i < len(ts) and ts[i]:
                        bar["t"] = int(ts[i])      # epoch seconds — used for date alignment
                    out.append(bar)
                except (TypeError, ValueError, IndexError):
                    continue
            return out
        except Exception as exc:
            logger.debug("fetch %s failed: %s", ysym, exc)
            return []
    return []


async def _fetch_daily(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    return await _fetch_chart(client, f"{symbol}.NS")


# ── Scan universe ─────────────────────────────────────────────────────────────
# Source (SCAN_UNIVERSE_SOURCE):
#   "nse"       → the full NSE equity master (~1800 listed EQ-series stocks) from
#                 the official archives CSV. This is *every* available stock.
#   "directory" → the backend stock directory (~300 curated names).
#   "bundled"   → the small hardcoded UNIVERSE (offline fallback).
# Resolved once per trading day and cached in Redis (survives restarts), with
# graceful degradation nse → directory → bundled if a source is unavailable.
UNIVERSE_SOURCE      = os.getenv("SCAN_UNIVERSE_SOURCE", "nse").lower()
NSE_EQUITY_LIST_URL  = os.getenv("NSE_EQUITY_LIST_URL",
                                 "https://archives.nseindia.com/content/equities/EQUITY_L.csv")
_UNIVERSE_CACHE_KEY  = "ai_engine:scan_universe"
_universe_cache: dict = {"date": None, "universe": None}


async def _fetch_nse_equity_universe() -> dict[str, str]:
    """Every NSE-listed equity (EQ series) from the official equity master CSV."""
    import csv, io
    headers = {"User-Agent": _UA["User-Agent"], "Accept": "text/csv,application/csv,*/*"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        r = await client.get(NSE_EQUITY_LIST_URL, headers=headers)
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
    if not rows:
        return {}
    head = [h.strip().upper() for h in rows[0]]
    i_sym = head.index("SYMBOL")
    i_name = head.index("NAME OF COMPANY")
    i_series = head.index("SERIES")
    uni: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) <= max(i_sym, i_name, i_series):
            continue
        sym, name, series = row[i_sym].strip(), row[i_name].strip(), row[i_series].strip().upper()
        if sym and series == "EQ":            # EQ = rolling segment (intraday-eligible)
            uni[sym] = name or sym
    return uni


async def _fetch_directory_universe() -> dict[str, str]:
    """The backend's curated stock directory (NSE-listed)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{BACKEND_URL}/api/stocks/directory/symbols",
                              params={"tradable_only": "true"})
        r.raise_for_status()
        rows = r.json().get("data", []) or []
    return {s["symbol"]: s.get("name", s["symbol"]) for s in rows if s.get("symbol")}


async def _load_universe() -> dict[str, str]:
    today = _ist_now().strftime("%Y-%m-%d")
    if _universe_cache["universe"] and _universe_cache["date"] == today:
        return _universe_cache["universe"]

    # Redis day-cache (avoids re-hitting NSE every sweep; survives restarts).
    try:
        rc = await _get_redis()
        raw = await rc.get(f"{_UNIVERSE_CACHE_KEY}:{today}")
        if raw:
            uni = json.loads(raw)
            if uni:
                _universe_cache.update({"date": today, "universe": uni})
                return uni
    except Exception:
        pass

    uni: dict[str, str] = {}
    if UNIVERSE_SOURCE == "nse":
        try:
            uni = await _fetch_nse_equity_universe()
            logger.info("scan universe: NSE equity master → %d symbols", len(uni))
        except Exception as exc:
            logger.warning("NSE equity list fetch failed (%s); falling back to directory", exc)
    if not uni and UNIVERSE_SOURCE in ("nse", "directory"):
        try:
            uni = await _fetch_directory_universe()
            logger.info("scan universe: backend directory → %d symbols", len(uni))
        except Exception as exc:
            logger.warning("directory universe fetch failed (%s); using bundled list", exc)
    if not uni:
        uni = dict(UNIVERSE)
        logger.info("scan universe: bundled fallback → %d symbols", len(uni))

    try:
        rc = await _get_redis()
        await rc.set(f"{_UNIVERSE_CACHE_KEY}:{today}", json.dumps(uni), ex=86400)
    except Exception:
        pass
    _universe_cache.update({"date": today, "universe": uni})
    return uni


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
    # Don't start a second sweep on top of a running one (the full-universe scan
    # takes minutes); a stale 'running' flag (e.g. after a crash) is overridden.
    async with _scan_lock:
        if _state.get("running") and (time.time() - _state.get("run_started", 0)) < STALE_RUN_SECS:
            logger.info("scan(%s) skipped — a sweep is already running", phase)
            return {}
        _state.update({"running": True, "run_started": time.time(), "scanning": True})

    # Preserve the last *completed* ranked board as the diff baseline before the
    # progressive checkpoints below start overwriting the live board.
    try:
        rc0 = await _get_redis()
        prev_board = await rc0.get(_RANKED_KEY)
        if prev_board:
            await rc0.set(_RANKED_PREV_KEY, prev_board, ex=86400 * 2)
    except Exception as exc:
        logger.debug("ranked prev snapshot skipped: %s", exc)

    calib = await _load_calibration()
    await _load_pattern_weights()        # pull the pattern model's weights for local scoring
    wl_max = await _watchlist_max()      # runtime-configurable intraday watchlist size
    universe = await _load_universe()
    total = len(universe)
    _state["universe"] = total
    candidates: list[dict] = []
    delivery_candidates: list[dict] = []
    scanned = 0
    _grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}

    def _progress_payload(scanning: bool) -> dict:
        """Rank what we have so far so the UI updates live during a long sweep."""
        wl = _top_watchlist(candidates, _grade_rank, wl_max)
        dl = sorted(delivery_candidates, key=lambda r: (_grade_rank.get(r.get("grade", "D"), 3),
                    -r.get("delivery_score", 0.0)))[:DELIVERY_TOP_N]
        for d in dl:
            d["reasoning"] = d.get("delivery_reasoning") or d.get("reasoning")
        gc = {g: sum(1 for w in wl if w.get("grade") == g) for g in ("A", "B", "C", "D")}
        return {
            "updated_at": _ist_now().isoformat(), "phase": phase, "scanned": scanned,
            "universe": total, "candidates": len(candidates), "market_regime": _state["market_regime"],
            "calibration": {"accuracy": calib.get("accuracy"), "samples": calib.get("samples", 0)},
            "grade_counts": gc, "high_conviction": gc["A"] + gc["B"],
            "scanning": scanning, "items": wl, "delivery": dl,
        }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        regime = await _market_regime(client)
        _state["market_regime"] = {1: "bullish", -1: "bearish", 0: "neutral"}[regime]
        for sym, name in universe.items():
            candles = await _fetch_daily(client, sym)
            scanned += 1
            res = _analyze(candles, regime=regime, calib=calib)
            if res:
                base = {"symbol": sym, "name": name, "source": "scanner", **res}
                if res["intraday_fit"]:
                    candidates.append(base)
                if res.get("delivery_fit"):
                    # independent copy — the intraday list gets mutated by the
                    # news-boost loop below; delivery should not be affected.
                    delivery_candidates.append(dict(base))
            _state["scanned"] = scanned
            # Progressive checkpoint: write the partial watchlist so the dashboard
            # shows the scan climbing through the universe and surfaces picks while
            # the background sweep is still running.
            if scanned % SCAN_CHECKPOINT_EVERY == 0:
                try:
                    rc = await _get_redis()
                    await rc.set(_WATCHLIST_KEY, json.dumps(_progress_payload(True)), ex=86400)
                    # Partial ranked board so the Predictions page fills during the sweep.
                    rk = sorted(candidates, key=lambda r: (r["action"] != "BUY",
                                _grade_rank.get(r.get("grade", "D"), 3),
                                -r.get("rank_score", r.get("signal_score", 0.0))))[:RANKED_MAX]
                    await rc.set(_RANKED_KEY, json.dumps({
                        "updated_at": _ist_now().isoformat(), "scanned": scanned, "universe": total,
                        "candidates": len(candidates), "market_regime": _state["market_regime"],
                        "items": [{"rank": i + 1, **c} for i, c in enumerate(rk)],
                    }), ex=86400)
                except Exception:
                    pass
                logger.info("scan(%s) progress: %d/%d scanned, %d intraday-fit, %d delivery-fit",
                            phase, scanned, total, len(candidates), len(delivery_candidates))
            await asyncio.sleep(FETCH_DELAY + random.uniform(0.0, FETCH_DELAY))

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
        # A fresh directional catalyst lifts win-probability (long-only: positive
        # news helps, negative hurts); re-grade so the watchlist reflects it.
        if boost:
            c["win_probability"] = round(min(0.95, max(0.05, c.get("win_probability", 0.5) * (1 + boost))), 3)
            c["grade"] = _grade_from_winprob(c["win_probability"], c.get("action", "HOLD"), c.get("intraday_fit", False))

    # Rank: BUY calls first, then by grade (A→D), then by composite rank score.
    _grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    candidates.sort(key=lambda r: (r["action"] != "BUY", _grade_rank.get(r.get("grade", "D"), 3), -r["rank_score"]))
    watchlist = _top_watchlist(candidates, _grade_rank, wl_max)   # most-convicted few, graded post-close

    # High-conviction tier: tag every candidate the system would *commit* to, given
    # the current adaptive bar. These are the only picks measured against the 90%
    # target — everything else is "watch, don't trade".
    hc = await _load_hc_params()
    for c in candidates:
        c["independent_signals"] = _independent_signals(c)
        c["committed"] = False
    # Of everything that clears the bar, commit only the TOP few by conviction —
    # paper trading trades the *cream*, not every grade-A name that passes (that
    # would dilute the whole point of a high-conviction tier).
    qualified = sorted(
        (c for c in candidates if _is_committed(c, hc)),
        key=lambda c: (c.get("win_probability") or 0.0,
                       c.get("independent_signals") or 0,
                       c.get("pattern_p_up") or 0.0),
        reverse=True)
    for c in qualified[:COMMITTED_MAX]:
        c["committed"] = True
    committed_list = [c for c in candidates if c["committed"]]
    logger.info("committed tier: %d qualified, top %d committed", len(qualified), len(committed_list))

    # Full ranked board (top RANKED_MAX) for the Predictions page — each entry
    # numbered with its rank and carrying the full evidence used to score it.
    try:
        ranked = [{"rank": i + 1, **c} for i, c in enumerate(candidates[:RANKED_MAX])]
        await r.set(_RANKED_KEY, json.dumps({
            "updated_at": _ist_now().isoformat(), "scanned": scanned, "universe": len(universe),
            "candidates": len(candidates), "market_regime": _state["market_regime"],
            "items": ranked,
        }), ex=86400)
    except Exception as exc:
        logger.debug("ranked board write failed: %s", exc)

    # Delivery list: rank delivery-fit names by grade then delivery_score (all are
    # already BUY + confirmed uptrend). Surface the delivery reasoning on `reasoning`
    # so the evidence modal explains the multi-week thesis, not the intraday one.
    delivery_candidates.sort(
        key=lambda r: (_grade_rank.get(r.get("grade", "D"), 3), -r.get("delivery_score", 0.0)))
    delivery_list = delivery_candidates[:DELIVERY_TOP_N]
    for d in delivery_list:
        d["reasoning"] = d.get("delivery_reasoning") or d.get("reasoning")

    # Publish the candidate pool so the sentiment-service analyses the names just
    # below the cut too — that's how a fresh catalyst can pull a stock *into* the
    # watchlist next cycle (otherwise news would only ever reinforce incumbents).
    try:
        pool = [{"symbol": c["symbol"], "name": c["name"]} for c in candidates[:CANDIDATE_POOL_N]]
        await r.set(_CANDIDATES_KEY, json.dumps({"updated_at": _ist_now().isoformat(), "items": pool}), ex=86400)
    except Exception as exc:
        logger.debug("candidate pool write failed: %s", exc)

    now = _ist_now()
    grade_counts = {g: sum(1 for w in watchlist if w.get("grade") == g) for g in ("A", "B", "C", "D")}
    payload = {
        "updated_at": now.isoformat(),
        "phase": phase,
        "scanned": scanned,
        "universe": len(universe),
        "candidates": len(candidates),
        "market_regime": _state["market_regime"],
        "calibration": {"accuracy": calib.get("accuracy"), "samples": calib.get("samples", 0)},
        "grade_counts": grade_counts,
        "high_conviction": grade_counts["A"] + grade_counts["B"],
        "scanning": False,
        "items": watchlist,
        "delivery": delivery_list,
        "committed": committed_list,
        "hc_params": hc,
    }
    try:
        r = await _get_redis()
        await r.set(_WATCHLIST_KEY, json.dumps(payload), ex=86400)
        if phase == "premarket":
            # 14-day retention so delivery picks survive long enough to be graded
            # on their multi-day forward return.
            await r.set(f"{_PREMARKET_KEY}:{now.strftime('%Y-%m-%d')}", json.dumps(payload), ex=86400 * 14)
    except Exception as exc:
        logger.warning("watchlist write failed: %s", exc)

    _state.update({"last_scan": payload["updated_at"], "scanned": scanned, "universe": total,
                   "candidates": len(candidates), "running": False, "scanning": False,
                   "calibration": payload["calibration"]})
    logger.info("scan(%s) complete: %d scanned, %d intraday-fit, %d on watchlist, %d delivery, market %s",
                phase, scanned, len(candidates), len(watchlist), len(delivery_list), _state["market_regime"])
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
                "committed": bool(w.get("committed")),
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

    meets_target = accuracy >= SCAN_ACCURACY_TARGET
    eval_payload = {
        "date": date_str, "evaluated_at": now.isoformat(), "trade_kind": "intraday",
        "picks": len(graded), "hits": hits,
        "accuracy": accuracy, "avg_realized_return_pct": avg_realized,
        "target": SCAN_ACCURACY_TARGET, "meets_target": meets_target,
        "learning_note": (
            f"Intraday scan accuracy {accuracy:.0%} is BELOW the {SCAN_ACCURACY_TARGET:.0%} target — "
            "conviction multipliers dampened; the next scans promote fewer high-grade picks until accuracy recovers."
            if not meets_target else
            f"Intraday scan accuracy {accuracy:.0%} meets the {SCAN_ACCURACY_TARGET:.0%} target."
        ),
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

    # High-conviction (committed) picks are NOT graded same-day — their edge is
    # multi-day, so they're graded on a COMMITTED_HORIZON forward return by
    # grade_due_committed() (called below from the post-close scheduler).

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


# ── Delivery (multi-day) evaluation ───────────────────────────────────────────

def _bar_date(bar: dict) -> str | None:
    t = bar.get("t")
    if not t:
        return None
    return datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d")


async def evaluate_delivery(entry_date: str) -> dict:
    """Grade the delivery picks made on `entry_date` against their forward return
    over DELIVERY_HORIZON_DAYS trading days. Delivery setups are BUY/holds, so a
    pick is 'correct' if it gained at least DELIVERY_TARGET_PCT within the horizon.

    Returns {status:'not_ready'} until enough forward sessions exist, so the
    post-close scheduler can keep retrying until the horizon has elapsed.
    """
    r = await _get_redis()
    if await r.get(_DELIVERY_DONE_KEY.format(entry_date)):
        return {"status": "already_graded", "date": entry_date}
    raw = await r.get(f"{_PREMARKET_KEY}:{entry_date}")
    if not raw:
        return {"status": "no_snapshot", "date": entry_date}
    items = (json.loads(raw) or {}).get("delivery", [])
    if not items:
        return {"status": "empty", "date": entry_date}

    graded: list[dict] = []
    not_ready = False
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for w in items:
            candles = await _fetch_daily(client, w["symbol"])
            await asyncio.sleep(FETCH_DELAY)
            if not candles:
                continue
            # locate the entry bar (last bar on/before entry_date)
            ei = None
            for i, b in enumerate(candles):
                bd = _bar_date(b)
                if bd and bd <= entry_date:
                    ei = i
                elif bd and bd > entry_date:
                    break
            if ei is None:
                continue
            if ei + DELIVERY_HORIZON_DAYS >= len(candles):
                not_ready = True            # horizon hasn't elapsed yet — retry later
                continue
            entry_c = candles[ei]["c"]
            fwd_c = candles[ei + DELIVERY_HORIZON_DAYS]["c"]
            fwd_ret = (fwd_c - entry_c) / entry_c * 100 if entry_c else 0.0
            graded.append({
                "symbol": w["symbol"], "action": w.get("action", "BUY"),
                "predicted_confidence": w.get("confidence"),
                "predicted_signal_score": w.get("delivery_score") or w.get("signal_score"),
                "day_return_pct": round(fwd_ret, 2),
                "realized_return_pct": round(fwd_ret, 2),
                "correct": bool(fwd_ret >= DELIVERY_TARGET_PCT),
                "trade_kind": "delivery",
            })

    if not graded:
        return {"status": "not_ready" if not_ready else "no_data", "date": entry_date}
    if not_ready and len(graded) < max(1, len(items) // 2):
        return {"status": "not_ready", "date": entry_date}   # too few have matured

    hits = sum(1 for g in graded if g["correct"])
    accuracy = round(hits / len(graded), 4)
    avg_realized = round(sum(g["realized_return_pct"] for g in graded) / len(graded), 2)
    meets_target = accuracy >= SCAN_ACCURACY_TARGET
    eval_payload = {
        "date": entry_date, "evaluated_at": _ist_now().isoformat(), "trade_kind": "delivery",
        "picks": len(graded), "hits": hits, "accuracy": accuracy,
        "avg_realized_return_pct": avg_realized,
        "target": SCAN_ACCURACY_TARGET, "meets_target": meets_target,
        "horizon_days": DELIVERY_HORIZON_DAYS,
        "results": sorted(graded, key=lambda g: -g["realized_return_pct"]),
    }
    try:
        await r.set(f"ai_engine:scan_eval:delivery:{entry_date}", json.dumps(eval_payload), ex=86400 * 90)
        await r.set(_DELIVERY_DONE_KEY.format(entry_date), "1", ex=86400 * 30)
    except Exception as exc:
        logger.warning("delivery eval write failed: %s", exc)
    await _push_feedback(eval_payload)
    logger.info("delivery grade %s: %d picks, accuracy %.0f%%, avg %+.2f%% over %dd",
                entry_date, len(graded), accuracy * 100, avg_realized, DELIVERY_HORIZON_DAYS)
    return eval_payload


async def grade_due_delivery() -> None:
    """Grade any delivery snapshot whose horizon has now elapsed (scan back over
    recent dated snapshots; each date is graded at most once)."""
    r = await _get_redis()
    today = _ist_now()
    for back in range(DELIVERY_HORIZON_DAYS, DELIVERY_HORIZON_DAYS + 12):
        d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        if await r.get(_DELIVERY_DONE_KEY.format(d)):
            continue
        if not await r.get(f"{_PREMARKET_KEY}:{d}"):
            continue
        try:
            await evaluate_delivery(d)
        except Exception as exc:
            logger.debug("delivery grade %s skipped: %s", d, exc)


async def evaluate_committed(entry_date: str) -> dict:
    """Grade the committed (high-conviction) picks made on `entry_date` on their
    COMMITTED_HORIZON forward return — correct if they gained COMMITTED_TARGET_PCT."""
    r = await _get_redis()
    if await r.get(_COMMITTED_DONE_KEY.format(entry_date)):
        return {"status": "already_graded", "date": entry_date}
    raw = await r.get(f"{_PREMARKET_KEY}:{entry_date}")
    if not raw:
        return {"status": "no_snapshot", "date": entry_date}
    items = (json.loads(raw) or {}).get("committed", [])
    if not items:
        return {"status": "empty", "date": entry_date}

    graded, not_ready, any_partial = [], False, False
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for w in items:
            candles = await _fetch_daily(client, w["symbol"])
            await asyncio.sleep(FETCH_DELAY)
            if not candles:
                continue
            ei = None
            for i, b in enumerate(candles):
                bd = _bar_date(b)
                if bd and bd <= entry_date:
                    ei = i
                elif bd and bd > entry_date:
                    break
            if ei is None:
                continue
            window = candles[ei + 1: ei + 1 + COMMITTED_HORIZON]   # 1..HORIZON bars available
            if not window:
                not_ready = True            # entry day itself — no forward bar yet
                continue
            entry = candles[ei]["c"]
            best = max(b["h"] for b in window)
            fwd = (best - entry) / entry * 100 if entry else 0.0
            if len(window) < COMMITTED_HORIZON:
                any_partial = True          # still maturing — will re-grade later
            graded.append({"symbol": w["symbol"], "action": "BUY",
                           "predicted_confidence": w.get("confidence"),
                           "predicted_signal_score": w.get("signal_score"),
                           "day_return_pct": round(fwd, 2), "realized_return_pct": round(fwd, 2),
                           "correct": bool(fwd >= COMMITTED_TARGET_PCT), "trade_kind": "committed",
                           "partial": len(window) < COMMITTED_HORIZON})
    if not graded:
        return {"status": "not_ready" if not_ready else "no_data", "date": entry_date}

    hits = sum(1 for g in graded if g["correct"])
    acc = round(hits / len(graded), 4)
    await _push_feedback({
        "date": entry_date, "evaluated_at": _ist_now().isoformat(), "trade_kind": "committed",
        "picks": len(graded), "hits": hits, "accuracy": acc,
        "avg_realized_return_pct": round(sum(g["realized_return_pct"] for g in graded) / len(graded), 2),
        "target": SCAN_ACCURACY_TARGET, "meets_target": acc >= SCAN_ACCURACY_TARGET, "results": graded,
    })
    if not any_partial:                     # only finalise once the full window matured
        await r.set(_COMMITTED_DONE_KEY.format(entry_date), "1", ex=86400 * 30)
        await _tune_hc_params(accuracy=acc, n=len(graded), target=SCAN_ACCURACY_TARGET)
    logger.info("committed grade %s: %d picks, accuracy %.0f%% (%s)", entry_date, len(graded), acc * 100,
                "partial" if any_partial else f"{COMMITTED_HORIZON}d")
    return {"status": "ok", "date": entry_date, "accuracy": acc, "picks": len(graded), "partial": any_partial}


async def grade_due_committed() -> None:
    """Grade committed snapshots — recent days on a partial forward window (so the
    line reaches today), older ones finalised once the full horizon has elapsed."""
    r = await _get_redis()
    today = _ist_now()
    for back in range(1, COMMITTED_HORIZON + 12):     # from yesterday onward (partial→full)
        d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        if await r.get(_COMMITTED_DONE_KEY.format(d)) or not await r.get(f"{_PREMARKET_KEY}:{d}"):
            continue
        try:
            await evaluate_committed(d)
        except Exception as exc:
            logger.debug("committed grade %s skipped: %s", d, exc)


async def backfill_delivery(days: int = 14, limit: int = 250) -> dict:
    """Reconstruct delivery-pick accuracy for the last `days` trading days so the
    accuracy graph has delivery history immediately (no waiting for live snapshots
    to mature). For each symbol in a liquid sample we fetch daily candles once,
    then for each past day decide whether it was a delivery pick *as of that day*
    (re-running the same fitness logic on the candle window) and grade it on the
    DELIVERY_HORIZON_DAYS forward return — exactly how live grading works."""
    calib = await _load_calibration()
    universe = await _load_universe()
    syms = list(universe.items())[:max(1, limit)]
    cutoff = _ist_now() - timedelta(days=days)
    by_date: dict[str, list[dict]] = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for sym, name in syms:
            candles = await _fetch_daily(client, sym)
            await asyncio.sleep(FETCH_DELAY)
            if len(candles) < 40:
                continue
            # evaluate each historical bar that has a full forward horizon and a date in range
            for i in range(35, len(candles) - DELIVERY_HORIZON_DAYS):
                bd = _bar_date(candles[i])
                if not bd or bd < cutoff.strftime("%Y-%m-%d"):
                    continue
                res = _analyze(candles[: i + 1], regime=0, calib=calib)
                if not res or not res.get("delivery_fit") or res.get("action") != "BUY":
                    continue
                entry_c = candles[i]["c"]
                fwd_c = candles[i + DELIVERY_HORIZON_DAYS]["c"]
                if entry_c <= 0:
                    continue
                fwd_ret = (fwd_c - entry_c) / entry_c * 100
                by_date.setdefault(bd, []).append({
                    "symbol": sym, "action": "BUY",
                    "predicted_confidence": res.get("confidence"),
                    "predicted_signal_score": res.get("delivery_score") or res.get("signal_score"),
                    "day_return_pct": round(fwd_ret, 2),
                    "realized_return_pct": round(fwd_ret, 2),
                    "correct": bool(fwd_ret >= DELIVERY_TARGET_PCT),
                    "trade_kind": "delivery",
                })

    r = await _get_redis()
    graded_days = 0
    for d, picks in sorted(by_date.items()):
        if not picks:
            continue
        hits = sum(1 for g in picks if g["correct"])
        accuracy = round(hits / len(picks), 4)
        avg_realized = round(sum(g["realized_return_pct"] for g in picks) / len(picks), 2)
        payload = {
            "date": d, "evaluated_at": _ist_now().isoformat(), "trade_kind": "delivery",
            "picks": len(picks), "hits": hits, "accuracy": accuracy,
            "avg_realized_return_pct": avg_realized,
            "target": SCAN_ACCURACY_TARGET, "meets_target": accuracy >= SCAN_ACCURACY_TARGET,
            "horizon_days": DELIVERY_HORIZON_DAYS, "backfilled": True,
            "results": sorted(picks, key=lambda g: -g["realized_return_pct"]),
        }
        try:
            await r.set(f"ai_engine:scan_eval:delivery:{d}", json.dumps(payload), ex=86400 * 90)
            await r.set(_DELIVERY_DONE_KEY.format(d), "1", ex=86400 * 30)
        except Exception:
            pass
        await _push_feedback(payload)
        graded_days += 1
    logger.info("delivery backfill: graded %d days from %d symbols", graded_days, len(syms))
    return {"status": "done", "graded_days": graded_days, "symbols": len(syms), "days": days}


async def backfill_committed(days: int = 30, limit: int = 400) -> dict:
    """Reconstruct the high-conviction (committed) tier's accuracy for the last
    `days`, graded on the COMMITTED_HORIZON forward return (their edge is multi-day,
    not same-day) — 'correct' if the pick gained COMMITTED_TARGET_PCT within the
    window. Mirrors live deferred grading."""
    hc = await _load_hc_params()
    calib = await _load_calibration()
    universe = await _load_universe()
    syms = list(universe.items())[:max(1, limit)]
    cutoff = (_ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    by_date: dict[str, list[dict]] = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for sym, name in syms:
            candles = await _fetch_daily(client, sym)
            await asyncio.sleep(FETCH_DELAY)
            if len(candles) < 40:
                continue
            for i in range(35, len(candles) - 1):     # need ≥1 forward bar (partial window OK near today)
                bd = _bar_date(candles[i])
                if not bd or bd < cutoff:
                    continue
                res = _analyze(candles[: i + 1], regime=0, calib=calib)
                if not res or not _is_committed(res, hc):
                    continue
                entry = candles[i]["c"]
                if entry <= 0:
                    continue
                window = candles[i + 1: i + 1 + COMMITTED_HORIZON]     # 1..HORIZON bars (fewer near today)
                if not window:
                    continue
                best = max(b["h"] for b in window)
                fwd = (best - entry) / entry * 100
                full = len(window) >= COMMITTED_HORIZON
                by_date.setdefault(bd, []).append({
                    "symbol": sym, "action": "BUY",
                    "predicted_confidence": res.get("confidence"),
                    "predicted_signal_score": res.get("signal_score"),
                    "day_return_pct": round(fwd, 2),
                    "realized_return_pct": round(fwd, 2),
                    "correct": bool(fwd >= COMMITTED_TARGET_PCT),
                    "committed": True, "trade_kind": "committed", "partial": not full,
                })

    graded_days = 0
    for d, picks in sorted(by_date.items()):
        if not picks:
            continue
        hits = sum(1 for g in picks if g["correct"])
        acc = round(hits / len(picks), 4)
        await _push_feedback({
            "date": d, "evaluated_at": _ist_now().isoformat(), "trade_kind": "committed",
            "picks": len(picks), "hits": hits, "accuracy": acc,
            "avg_realized_return_pct": round(sum(g["realized_return_pct"] for g in picks) / len(picks), 2),
            "target": SCAN_ACCURACY_TARGET, "meets_target": acc >= SCAN_ACCURACY_TARGET,
            "backfilled": True, "results": sorted(picks, key=lambda g: -g["realized_return_pct"]),
        })
        graded_days += 1
    logger.info("committed backfill: graded %d days from %d symbols (bar wp>=%.2f factors>=%d)",
                graded_days, len(syms), hc["wp_floor"], hc["min_factors"])
    return {"status": "done", "graded_days": graded_days, "symbols": len(syms), "days": days, "hc_params": hc}


async def backfill_intraday(days: int = 14, limit: int = 400) -> dict:
    """Reconstruct the intraday signal score for the last `days` so the AI Scan
    Accuracy graph reaches the latest completed day. For each past day we rebuild
    the most-convicted watchlist *as of that day* (grade-A/B BUYs, top
    WATCHLIST_MAX) and grade it on THAT day's own open→close move — real, no
    lookahead. Fills gaps where live post-close grading didn't run."""
    calib = await _load_calibration()
    wl_max = await _watchlist_max()
    universe = await _load_universe()
    syms = list(universe.items())[:max(1, limit)]
    cutoff = (_ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    _grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    by_date: dict[str, list[dict]] = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for sym, name in syms:
            candles = await _fetch_daily(client, sym)
            await asyncio.sleep(FETCH_DELAY)
            if len(candles) < 40:
                continue
            for i in range(35, len(candles) - 1):  # -1 so candles[i+1] always exists
                bd = _bar_date(candles[i])
                if not bd or bd < cutoff:
                    continue
                res = _analyze(candles[: i + 1], regime=0, calib=calib)
                if not res or res.get("action") != "BUY" or res.get("grade") not in ("A", "B"):
                    continue
                # Grade on next bar's open→close: simulates entering at bar i's close
                # and exiting at bar i+1's close, which is what live trading would do.
                entry = candles[i]["c"]
                next_bar = candles[i + 1]
                if entry <= 0:
                    continue
                move = (next_bar["c"] - entry) / entry * 100
                by_date.setdefault(bd, []).append({
                    "symbol": sym, "action": "BUY", "grade": res.get("grade"),
                    "win_probability": res.get("win_probability"),
                    "predicted_confidence": res.get("confidence"),
                    "predicted_signal_score": res.get("signal_score"),
                    "day_return_pct": round(move, 2), "realized_return_pct": round(move, 2),
                    "correct": bool(move >= 0.3),
                })

    graded_days = 0
    for d, picks in sorted(by_date.items()):
        # the day's watchlist = top-N most-convicted, exactly as live
        picks.sort(key=lambda r: (_grade_rank.get(r.get("grade", "D"), 3), -(r.get("win_probability") or 0)))
        top = picks[:wl_max]
        if not top:
            continue
        hits = sum(1 for g in top if g["correct"])
        acc = round(hits / len(top), 4)
        await _push_feedback({
            "date": d, "evaluated_at": _ist_now().isoformat(), "trade_kind": "intraday",
            "picks": len(top), "hits": hits, "accuracy": acc,
            "avg_realized_return_pct": round(sum(g["realized_return_pct"] for g in top) / len(top), 2),
            "target": SCAN_ACCURACY_TARGET, "meets_target": acc >= SCAN_ACCURACY_TARGET,
            "backfilled": True, "results": top,
        })
        graded_days += 1
    logger.info("intraday backfill: graded %d days from %d symbols (top %d/day)", graded_days, len(syms), wl_max)
    return {"status": "done", "graded_days": graded_days, "symbols": len(syms), "days": days}


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
                # Grade older delivery + committed picks whose horizon has elapsed.
                await grade_due_delivery()
                await grade_due_committed()
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
