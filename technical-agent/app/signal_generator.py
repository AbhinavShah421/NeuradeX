"""Generate BUY/SELL/HOLD signal from indicators + optional ML model."""

from app.indicators import compute_indicators, build_feature_vector
from app.ml_models import get_model, predict_xgboost
from app.config import settings
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def _rule_based_signal(ind: dict) -> tuple[str, float, str]:
    """Deterministic rule-based signal when ML model is unavailable."""
    score = 0
    reasons = []

    rsi = ind.get("rsi_14", 50)
    if rsi < 30:
        score += 2
        reasons.append(f"RSI oversold ({rsi:.1f})")
    elif rsi > 70:
        score -= 2
        reasons.append(f"RSI overbought ({rsi:.1f})")

    macd_hist = ind.get("macd_hist", 0)
    if macd_hist > 0:
        score += 1
        reasons.append("MACD bullish")
    elif macd_hist < 0:
        score -= 1
        reasons.append("MACD bearish")

    close = ind.get("close", 0)
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    ema200 = ind.get("ema_200")
    if ema200 and close > ema200:
        score += 1
        reasons.append("above EMA200")
    elif ema200 and close < ema200:
        score -= 1
        reasons.append("below EMA200")
    if ema20 and ema50 and ema20 > ema50:
        score += 1
        reasons.append("EMA20 > EMA50 (bullish)")
    elif ema20 and ema50 and ema20 < ema50:
        score -= 1
        reasons.append("EMA20 < EMA50 (bearish)")

    bb_pos = ind.get("bb_position", 0.5)
    if bb_pos < 0.1:
        score += 1
        reasons.append("near BB lower band")
    elif bb_pos > 0.9:
        score -= 1
        reasons.append("near BB upper band")

    vol_ratio = ind.get("volume_ratio", 1.0)
    if vol_ratio > 1.5:
        reasons.append(f"high volume ({vol_ratio:.1f}x avg)")

    if score >= 3:
        signal = "BUY"
        confidence = min(0.55 + (score - 3) * 0.05, 0.85)
    elif score <= -3:
        signal = "SELL"
        confidence = min(0.55 + (-score - 3) * 0.05, 0.85)
    else:
        signal = "HOLD"
        confidence = 0.50 + (3 - abs(score)) * 0.05

    return signal, round(confidence, 3), "; ".join(reasons) or "no strong signal"


def generate_signal(candles: list[dict], symbol: str) -> dict:
    if not candles:
        return {
            "symbol": symbol, "signal": "HOLD", "confidence": 0.5,
            "reasoning": "no candle data", "indicators": {}, "model_votes": {},
        }

    df = pd.DataFrame(candles)
    df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    indicators = compute_indicators(df)
    if not indicators:
        return {
            "symbol": symbol, "signal": "HOLD", "confidence": 0.5,
            "reasoning": "insufficient data for indicators", "indicators": {}, "model_votes": {},
        }

    model_votes: dict = {}
    final_signal = "HOLD"
    final_confidence = 0.5

    model = get_model(settings.MLFLOW_TRACKING_URI)
    if model:
        features = build_feature_vector(indicators)
        xgb_signal, xgb_conf = predict_xgboost(model, features)
        model_votes["xgboost"] = {"signal": xgb_signal, "confidence": xgb_conf}

    rule_signal, rule_conf, rule_reason = _rule_based_signal(indicators)
    model_votes["rule_based"] = {"signal": rule_signal, "confidence": rule_conf}

    if model_votes.get("xgboost"):
        xgb = model_votes["xgboost"]
        # Blend: 60% ML, 40% rule-based
        signals = [xgb["signal"], rule_signal]
        if xgb["signal"] == rule_signal:
            final_signal = xgb["signal"]
            final_confidence = round(0.6 * xgb["confidence"] + 0.4 * rule_conf, 3)
            reasoning = f"XGBoost+rules agree: {rule_reason}"
        elif xgb["confidence"] > rule_conf:
            final_signal = xgb["signal"]
            final_confidence = round(xgb["confidence"] * 0.7, 3)
            reasoning = f"XGBoost dominant ({xgb['confidence']:.2f}): {rule_reason}"
        else:
            final_signal = rule_signal
            final_confidence = round(rule_conf * 0.7, 3)
            reasoning = f"Rules dominant: {rule_reason}"
    else:
        final_signal = rule_signal
        final_confidence = rule_conf
        reasoning = rule_reason

    return {
        "symbol": symbol,
        "signal": final_signal,
        "confidence": final_confidence,
        "reasoning": reasoning,
        "indicators": indicators,
        "model_votes": model_votes,
    }
