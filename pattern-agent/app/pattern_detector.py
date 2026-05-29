"""Candlestick pattern detection + HMM regime classification."""

import logging
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_hmm_model = None


def _load_hmm(n_components: int = 3):
    global _hmm_model
    if _hmm_model is not None:
        return _hmm_model
    try:
        from hmmlearn import hmm
        model = hmm.GaussianHMM(n_components=n_components, covariance_type="diag", n_iter=100)
        _hmm_model = model
    except Exception as exc:
        logger.warning("HMM load failed: %s", exc)
    return _hmm_model


def detect_candlestick_patterns(df: pd.DataFrame) -> list[dict]:
    """Detects common single and multi-candle patterns on the last 5 candles."""
    if len(df) < 5:
        return []

    patterns = []
    last = df.iloc[-1]
    prev = df.iloc[-2]

    def body(c): return abs(float(c["close"]) - float(c["open"]))
    def upper_wick(c): return float(c["high"]) - max(float(c["close"]), float(c["open"]))
    def lower_wick(c): return min(float(c["close"]), float(c["open"])) - float(c["low"])
    def is_bullish(c): return float(c["close"]) > float(c["open"])
    def is_bearish(c): return float(c["close"]) < float(c["open"])
    def candle_range(c): return float(c["high"]) - float(c["low"])

    # Doji
    if body(last) <= candle_range(last) * 0.1:
        patterns.append({"pattern": "doji", "direction": "neutral", "strength": 0.5})

    # Hammer (bullish reversal at bottom)
    if lower_wick(last) >= body(last) * 2 and upper_wick(last) < body(last) * 0.5:
        patterns.append({"pattern": "hammer", "direction": "bullish", "strength": 0.65})

    # Shooting star (bearish reversal at top)
    if upper_wick(last) >= body(last) * 2 and lower_wick(last) < body(last) * 0.5:
        patterns.append({"pattern": "shooting_star", "direction": "bearish", "strength": 0.65})

    # Bullish engulfing
    if (is_bullish(last) and is_bearish(prev)
            and float(last["open"]) < float(prev["close"])
            and float(last["close"]) > float(prev["open"])):
        patterns.append({"pattern": "bullish_engulfing", "direction": "bullish", "strength": 0.75})

    # Bearish engulfing
    if (is_bearish(last) and is_bullish(prev)
            and float(last["open"]) > float(prev["close"])
            and float(last["close"]) < float(prev["open"])):
        patterns.append({"pattern": "bearish_engulfing", "direction": "bearish", "strength": 0.75})

    # Bullish marubozu (strong bull candle, almost no wicks)
    if is_bullish(last) and body(last) > candle_range(last) * 0.85:
        patterns.append({"pattern": "bullish_marubozu", "direction": "bullish", "strength": 0.70})

    # Bearish marubozu
    if is_bearish(last) and body(last) > candle_range(last) * 0.85:
        patterns.append({"pattern": "bearish_marubozu", "direction": "bearish", "strength": 0.70})

    # Morning star (3-candle bullish reversal)
    if len(df) >= 3:
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if is_bearish(c1) and body(c2) < body(c1) * 0.3 and is_bullish(c3):
            patterns.append({"pattern": "morning_star", "direction": "bullish", "strength": 0.80})

    # Evening star (3-candle bearish reversal)
    if len(df) >= 3:
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if is_bullish(c1) and body(c2) < body(c1) * 0.3 and is_bearish(c3):
            patterns.append({"pattern": "evening_star", "direction": "bearish", "strength": 0.80})

    return patterns


def classify_regime_hmm(df: pd.DataFrame) -> str:
    """Uses HMM on returns to classify regime: bull/bear/sideways."""
    if len(df) < 20:
        return "UNKNOWN"
    try:
        model = _load_hmm(n_components=3)
        if model is None:
            return _simple_regime(df)

        returns = df["close"].pct_change().dropna().values.reshape(-1, 1)
        if len(returns) < 10:
            return _simple_regime(df)

        model.fit(returns)
        states = model.predict(returns)
        state_means = {}
        for s in range(model.n_components):
            mask = states == s
            if mask.sum() > 0:
                state_means[s] = float(returns[mask].mean())

        current_state = int(states[-1])
        mean_return = state_means.get(current_state, 0)

        if mean_return > 0.001:
            return "BULL"
        elif mean_return < -0.001:
            return "BEAR"
        return "SIDEWAYS"
    except Exception:
        return _simple_regime(df)


def _simple_regime(df: pd.DataFrame) -> str:
    if len(df) < 10:
        return "UNKNOWN"
    close = df["close"].astype(float)
    slope = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10]
    if slope > 0.03:
        return "BULL"
    elif slope < -0.03:
        return "BEAR"
    return "SIDEWAYS"


def generate_pattern_signal(candles: list[dict], symbol: str) -> dict:
    if not candles or len(candles) < 5:
        return {
            "symbol": symbol, "signal": "HOLD", "confidence": 0.50,
            "reasoning": "insufficient candles for pattern detection",
            "patterns": [], "regime": "UNKNOWN",
        }

    df = pd.DataFrame(candles)
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    patterns = detect_candlestick_patterns(df)
    regime = classify_regime_hmm(df)

    bull_strength = sum(p["strength"] for p in patterns if p["direction"] == "bullish")
    bear_strength = sum(p["strength"] for p in patterns if p["direction"] == "bearish")

    regime_bias = 0
    if regime == "BULL":
        regime_bias = 1
    elif regime == "BEAR":
        regime_bias = -1

    total_bull = bull_strength + (0.3 if regime_bias > 0 else 0)
    total_bear = bear_strength + (0.3 if regime_bias < 0 else 0)

    if total_bull > total_bear and total_bull > 0.4:
        signal = "BUY"
        confidence = min(0.50 + total_bull * 0.15, 0.85)
        reason = f"Bullish patterns: {[p['pattern'] for p in patterns if p['direction']=='bullish']}; regime={regime}"
    elif total_bear > total_bull and total_bear > 0.4:
        signal = "SELL"
        confidence = min(0.50 + total_bear * 0.15, 0.85)
        reason = f"Bearish patterns: {[p['pattern'] for p in patterns if p['direction']=='bearish']}; regime={regime}"
    else:
        signal = "HOLD"
        confidence = 0.50
        reason = f"No clear pattern signal; regime={regime}"

    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": round(confidence, 3),
        "reasoning": reason,
        "patterns": patterns,
        "regime": regime,
    }
