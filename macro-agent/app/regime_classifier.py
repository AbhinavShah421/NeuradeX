"""Classify macro regime and generate trading signal from macro indicators."""

from typing import Literal

Regime = Literal["RISK_ON", "RISK_OFF", "NEUTRAL", "HIGH_VOLATILITY"]


def classify_regime(indicators: dict) -> tuple[Regime, str]:
    vix = indicators.get("india_vix", 15)
    usd_inr = indicators.get("usd_inr", 83)
    crude = indicators.get("crude_brent", 80)
    gsec = indicators.get("gsec_10y", 7)
    reasons = []
    risk_score = 0   # positive = risk-on, negative = risk-off

    if vix > 25:
        risk_score -= 3
        reasons.append(f"VIX high ({vix:.1f})")
    elif vix > 20:
        risk_score -= 1
        reasons.append(f"VIX elevated ({vix:.1f})")
    elif vix < 14:
        risk_score += 2
        reasons.append(f"VIX low ({vix:.1f}) — calm market")

    if usd_inr > 85:
        risk_score -= 2
        reasons.append(f"INR weak ({usd_inr:.1f})")
    elif usd_inr < 82:
        risk_score += 1
        reasons.append(f"INR strong ({usd_inr:.1f})")

    if crude > 95:
        risk_score -= 2
        reasons.append(f"crude high ({crude:.1f}) — inflationary pressure")
    elif crude < 70:
        risk_score += 1
        reasons.append(f"crude low ({crude:.1f}) — benign inflation")

    if gsec > 7.5:
        risk_score -= 1
        reasons.append(f"yields high ({gsec:.2f}%)")

    if vix > 30:
        return "HIGH_VOLATILITY", "; ".join(reasons)
    if risk_score >= 2:
        return "RISK_ON", "; ".join(reasons)
    if risk_score <= -2:
        return "RISK_OFF", "; ".join(reasons)
    return "NEUTRAL", "; ".join(reasons) or "macro in equilibrium"


def generate_macro_signal(indicators: dict) -> dict:
    regime, reason = classify_regime(indicators)

    regime_signals = {
        "RISK_ON":       ("BUY",  0.65),
        "NEUTRAL":       ("HOLD", 0.55),
        "RISK_OFF":      ("SELL", 0.60),
        "HIGH_VOLATILITY": ("HOLD", 0.50),
    }
    signal, confidence = regime_signals[regime]

    return {
        "signal": signal,
        "confidence": confidence,
        "reasoning": f"Regime: {regime} — {reason}",
        "indicators": indicators,
        "regime": regime,
    }
