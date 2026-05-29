"""Volatility Agent — ATR, Bollinger Band width, regime detection, risk scoring."""
from __future__ import annotations
import math
from .base import AgentSignal, BaseAgent


class VolatilityAgent(BaseAgent):
    name = "volatility"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 14:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.35,
                reasoning="Insufficient data for volatility analysis",
                indicators={"risk_score": 0.50},
            )

        closes = [c["close"] for c in candles]

        # ATR (14-period average true range)
        atr      = self._atr(candles, 14)
        atr_pct  = atr / closes[-1] * 100 if closes[-1] > 0 else 0

        # Bollinger Band width (% of price)
        w      = closes[-20:] if len(closes) >= 20 else closes
        mean_  = sum(w) / len(w)
        std_   = math.sqrt(sum((c - mean_) ** 2 for c in w) / len(w))
        bb_width_pct = std_ / mean_ * 100 if mean_ > 0 else 0

        signals: dict = {
            "atr":           round(atr, 3),
            "atr_pct":       round(atr_pct, 3),
            "bb_width_pct":  round(bb_width_pct, 3),
        }

        score = 0.0

        if atr_pct > 3.0:
            signals["regime"] = "high_volatility"
            risk_score = 0.85
            score = 0.0  # stay neutral when market is chaotic
        elif atr_pct > 1.5:
            signals["regime"] = "moderate_volatility"
            risk_score = 0.55
            # Follow recent trend in moderate vol
            if len(closes) >= 5:
                recent = (closes[-1] - closes[-5]) / closes[-5]
                score  = 0.12 * (1 if recent > 0 else -1)
        else:
            signals["regime"] = "low_volatility"
            risk_score = 0.25
            # Favour breakout direction in low vol
            if len(closes) >= 5:
                hi, lo = max(closes[-5:]), min(closes[-5:])
                mid    = (hi + lo) / 2
                score  = 0.10 * (1 if closes[-1] > mid else -1)

        signals["risk_score"] = round(risk_score, 3)

        if score > 0.05:
            action = "BUY";  confidence = min(0.85, 0.50 + score * 3)
        elif score < -0.05:
            action = "SELL"; confidence = min(0.85, 0.50 + abs(score) * 3)
        else:
            action = "HOLD"; confidence = 0.50 + (1 - risk_score) * 0.25

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(confidence, 3),
            reasoning=(
                f"{signals['regime']}, ATR {atr_pct:.2f}% | "
                f"Risk score: {risk_score:.2f}"
            ),
            indicators=signals,
        )

    @staticmethod
    def _atr(candles: list[dict], period: int) -> float:
        trs = []
        for i in range(1, len(candles)):
            prev = candles[i - 1]["close"]
            h, l = candles[i]["high"], candles[i]["low"]
            trs.append(max(h - l, abs(h - prev), abs(l - prev)))
        if not trs:
            return 0.0
        window = trs[-period:]
        return sum(window) / len(window)
