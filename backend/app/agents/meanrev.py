"""Mean-Reversion Agent — the ensemble's counter-trend voice.

Most existing voices (momentum, technical trend, RL) are trend-following. In
range-bound / choppy markets that herd is wrong together. This agent fades
over-extension: when price stretches far from its mean (Bollinger %b, z-score)
and the move looks exhausted (RSI extreme), it leans the OTHER way — BUY into
washed-out dips, SELL into blow-off tops. The Market-Regime model up-weights it
in chop and down-weights it in strong trends.
"""
from __future__ import annotations
import math

from .base import AgentSignal, BaseAgent


class MeanReversionAgent(BaseAgent):
    name = "meanrev"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        closes = [c["close"] for c in candles if c.get("close")]
        if len(closes) < 25:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for mean-reversion")

        idx = len(closes) - 1
        price = closes[idx]
        win = closes[-20:]
        mean = sum(win) / len(win)
        var = sum((x - mean) ** 2 for x in win) / len(win)
        sd = math.sqrt(var)
        if sd <= 1e-9:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Flat series — no dispersion to revert to")

        z = (price - mean) / sd                       # standardized distance from mean
        bb_pct = (price - (mean - 2 * sd)) / (4 * sd)  # Bollinger %b (0=lower, 1=upper)
        rsi = self._rsi(closes, 14)

        score = 0.0
        sig: dict = {"z_score": round(z, 2), "bb_pct": round(bb_pct, 2), "rsi": round(rsi, 1)}

        # Stretched BELOW the mean → fade short / lean long (mean-revert up)
        if z <= -2.0:
            score += 0.45
        elif z <= -1.2:
            score += 0.22
        # Stretched ABOVE the mean → fade long / lean short (mean-revert down)
        if z >= 2.0:
            score -= 0.45
        elif z >= 1.2:
            score -= 0.22

        if rsi <= 25:
            score += 0.25
        elif rsi <= 35:
            score += 0.12
        if rsi >= 75:
            score -= 0.25
        elif rsi >= 65:
            score -= 0.12

        # Require a flicker of stabilisation before buying the dip (last bar up),
        # and of rollover before fading the top (last bar down).
        if idx >= 1:
            last_up = closes[idx] >= closes[idx - 1]
            if score > 0 and not last_up:
                score *= 0.6
            if score < 0 and last_up:
                score *= 0.6

        if score >= 0.30:
            action, conf = "BUY", min(0.9, 0.5 + score)
            reason = f"Mean-reversion: oversold (z {z:.1f}, RSI {rsi:.0f}) — fade the drop."
        elif score <= -0.30:
            action, conf = "SELL", min(0.9, 0.5 + abs(score))
            reason = f"Mean-reversion: overbought (z {z:.1f}, RSI {rsi:.0f}) — fade the spike."
        else:
            action, conf = "HOLD", 0.4
            reason = f"Near the mean (z {z:.1f}) — no reversion edge."

        return AgentSignal(agent_name=self.name, action=action, confidence=round(conf, 3),
                           reasoning=reason, indicators=sig)

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float:
        if len(closes) <= period:
            return 50.0
        gains = losses = 0.0
        for i in range(len(closes) - period, len(closes)):
            ch = closes[i] - closes[i - 1]
            if ch >= 0:
                gains += ch
            else:
                losses -= ch
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
