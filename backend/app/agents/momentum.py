"""Momentum Agent — Rate-of-Change, volume surge, stochastic, price acceleration."""
from __future__ import annotations
from .base import AgentSignal, BaseAgent


class MomentumAgent(BaseAgent):
    name = "momentum"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 10:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for momentum analysis")

        closes  = [c["close"]         for c in candles]
        volumes = [c.get("volume", 0) for c in candles]
        idx     = len(closes) - 1

        score   = 0.0
        signals: dict = {}

        # Rate of Change 5 (%)
        if idx >= 5:
            roc5 = (closes[idx] - closes[idx - 5]) / closes[idx - 5] * 100
            signals["roc5"] = round(roc5, 3)
            if roc5 > 2.0:
                score += 0.30; signals["roc5_signal"] = "strong_up"
            elif roc5 > 0.5:
                score += 0.15; signals["roc5_signal"] = "moderate_up"
            elif roc5 < -2.0:
                score -= 0.30; signals["roc5_signal"] = "strong_down"
            elif roc5 < -0.5:
                score -= 0.15; signals["roc5_signal"] = "moderate_down"
            else:
                signals["roc5_signal"] = "neutral"

        # Volume surge (current vs 10-bar avg)
        if len(volumes) >= 10 and sum(volumes[-10:]) > 0:
            avg_vol   = sum(volumes[-10:]) / 10
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
            signals["volume_ratio"] = round(vol_ratio, 2)
            if vol_ratio > 2.0:
                direction = 1 if closes[-1] >= closes[-2] else -1
                score    += 0.25 * direction
                signals["volume"] = "surge"
            elif vol_ratio < 0.5:
                score    *= 0.80
                signals["volume"] = "dry"
            else:
                signals["volume"] = "normal"

        # Price acceleration (d²price/dt²)
        if idx >= 3:
            d1    = closes[-1] - closes[-2]
            d2    = closes[-2] - closes[-3]
            accel = d1 - d2
            signals["acceleration"] = round(accel, 4)
            if accel > 0 and d1 > 0:
                score += 0.10; signals["accel_signal"] = "accelerating_up"
            elif accel < 0 and d1 < 0:
                score -= 0.10; signals["accel_signal"] = "accelerating_down"

        # Stochastic %K (14)
        if len(candles) >= 14:
            highs = [c["high"] for c in candles[-14:]]
            lows  = [c["low"]  for c in candles[-14:]]
            hh, ll = max(highs), min(lows)
            stoch  = (closes[-1] - ll) / (hh - ll) * 100 if (hh - ll) > 0 else 50.0
            signals["stoch"] = round(stoch, 1)
            if stoch < 20:
                score += 0.15; signals["stoch_signal"] = "oversold"
            elif stoch > 80:
                score -= 0.15; signals["stoch_signal"] = "overbought"
            else:
                signals["stoch_signal"] = "neutral"

        score = max(-1.0, min(1.0, score))

        if score > 0.15:
            action = "BUY";  confidence = min(0.93, 0.50 + score * 0.50)
        elif score < -0.15:
            action = "SELL"; confidence = min(0.93, 0.50 + abs(score) * 0.50)
        else:
            action = "HOLD"; confidence = 0.50

        reasons = []
        if signals.get("roc5_signal") not in (None, "neutral"):
            reasons.append(f"ROC5 {signals.get('roc5', 0):+.1f}% ({signals['roc5_signal']})")
        if signals.get("volume") == "surge":
            reasons.append(f"Volume surge {signals.get('volume_ratio', 0):.1f}×")
        if signals.get("stoch_signal") in ("oversold", "overbought"):
            reasons.append(f"Stoch {signals.get('stoch', 50):.0f} ({signals['stoch_signal']})")

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(confidence, 3),
            reasoning="; ".join(reasons) or f"Momentum score {score:.2f}",
            indicators=signals,
        )
