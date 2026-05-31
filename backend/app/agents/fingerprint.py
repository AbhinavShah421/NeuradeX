"""Market-situation fingerprinting.

Turns a window of candles into a fixed-length, scale-free vector so two
situations that *look alike* (similar price shape + indicator context) land
close together in vector space. This is the substrate the Pattern Memory bank
searches over: "have I seen this exact kind of setup before, and what happened?"

The vector is deliberately scale-free (returns + normalised indicators), so a
fingerprint built from daily candles is comparable to one from intraday candles.
"""
from __future__ import annotations
import math
from typing import Optional

# Number of trailing normalised returns that capture the recent price *shape*.
SHAPE_LEN = 10
# Total fingerprint dimensionality = SHAPE_LEN shape returns + context features.
_CONTEXT_FEATURES = 9
FINGERPRINT_DIM = SHAPE_LEN + _CONTEXT_FEATURES


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    ag, al = gains / period, losses / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def build_fingerprint(candles: list[dict]) -> Optional[list[float]]:
    """Return a FINGERPRINT_DIM-length vector for the latest situation, or None
    if there aren't enough candles to characterise it (<15)."""
    if not candles or len(candles) < 15:
        return None

    closes = [float(c["close"]) for c in candles]
    highs  = [float(c.get("high", c["close"])) for c in candles]
    lows   = [float(c.get("low",  c["close"])) for c in candles]
    vols   = [float(c.get("volume", 0) or 0) for c in candles]
    last   = closes[-1]

    # ── Shape: last SHAPE_LEN bar-to-bar returns, scaled by a typical move ──────
    rets: list[float] = []
    for i in range(len(closes) - SHAPE_LEN, len(closes)):
        prev = closes[i - 1] if i > 0 else closes[i]
        rets.append(_safe_div(closes[i] - prev, prev))
    # Normalise the shape by its own volatility so the *pattern* matters, not size
    sigma = (sum(r * r for r in rets) / len(rets)) ** 0.5 or 1e-6
    shape = [_clip(r / (sigma * 3)) for r in rets]

    # ── Context features (each roughly in [-1, 1]) ──────────────────────────────
    rsi = _rsi(closes)
    f_rsi = _clip((rsi - 50) / 50)

    e12, e26 = _ema(closes[-26:], 12), _ema(closes[-26:], 26)
    f_macd = _clip(_safe_div(e12 - e26, last) * 100)

    vwap = sum((highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))) / len(closes)
    f_vwap = _clip(_safe_div(last - vwap, vwap) * 50)

    atr = sum(highs[i] - lows[i] for i in range(len(closes) - 10, len(closes))) / 10
    f_atr = _clip(_safe_div(atr, last) * 50)

    # Bollinger %B style position over last 20
    win = closes[-20:]
    mean = sum(win) / len(win)
    std = (sum((c - mean) ** 2 for c in win) / len(win)) ** 0.5 or 1e-6
    f_bbpos = _clip((last - mean) / (2 * std))

    avg_vol = (sum(vols) / len(vols)) or 1.0
    f_vol = _clip(math.tanh(_safe_div(vols[-1], avg_vol) - 1.0))

    f_mom5  = _clip(_safe_div(last - closes[-5],  closes[-5])  * 20) if len(closes) >= 5  else 0.0
    f_mom10 = _clip(_safe_div(last - closes[-10], closes[-10]) * 20) if len(closes) >= 10 else 0.0

    # Trend slope of last 10 closes (sign + magnitude)
    n = min(10, len(closes))
    xs = list(range(n))
    ys = closes[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1e-6
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    f_slope = _clip(_safe_div(slope, last) * 200)

    context = [f_rsi, f_macd, f_vwap, f_atr, f_bbpos, f_vol, f_mom5, f_mom10, f_slope]
    return shape + context


def classify_regime(candles: list[dict]) -> str:
    """Coarse regime label so memory retrieval only compares like-with-like.
    Format: '<trend>_<vol>' e.g. 'up_high', 'flat_low', 'down_med'."""
    if not candles or len(candles) < 10:
        return "unknown"
    closes = [float(c["close"]) for c in candles]
    highs  = [float(c.get("high", c["close"])) for c in candles]
    lows   = [float(c.get("low",  c["close"])) for c in candles]
    last = closes[-1]

    mom = _safe_div(last - closes[-10], closes[-10]) * 100
    trend = "up" if mom > 1.0 else ("down" if mom < -1.0 else "flat")

    atr = sum(highs[i] - lows[i] for i in range(len(closes) - 10, len(closes))) / 10
    atr_pct = _safe_div(atr, last) * 100
    vol = "low" if atr_pct < 0.8 else ("high" if atr_pct > 2.0 else "med")
    return f"{trend}_{vol}"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1e-9
    nb = sum(y * y for y in b) ** 0.5 or 1e-9
    return dot / (na * nb)
