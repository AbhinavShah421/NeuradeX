"""Pattern Recognition Agent — candlestick patterns, trend direction."""
from __future__ import annotations
from .base import AgentSignal, BaseAgent


class PatternAgent(BaseAgent):
    name = "pattern"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Not enough candles for pattern analysis")

        c = candles[-1]
        p = candles[-2]

        score    = 0.0
        patterns: list[str] = []

        # Trend (last 10 bars)
        if len(candles) >= 10:
            w     = [x["close"] for x in candles[-10:]]
            trend = (w[-1] - w[0]) / w[0] if w[0] > 0 else 0
            if trend > 0.015:
                score += 0.20; patterns.append("uptrend")
            elif trend < -0.015:
                score -= 0.20; patterns.append("downtrend")

        # Bullish engulfing
        if (p["close"] < p["open"] and c["close"] > c["open"]
                and c["open"] < p["close"] and c["close"] > p["open"]):
            score += 0.40; patterns.append("bullish_engulfing")

        # Bearish engulfing
        elif (p["close"] > p["open"] and c["close"] < c["open"]
              and c["open"] > p["close"] and c["close"] < p["open"]):
            score -= 0.40; patterns.append("bearish_engulfing")

        # Hammer (bullish reversal)
        body        = abs(c["close"] - c["open"])
        lower_wick  = min(c["close"], c["open"]) - c["low"]
        upper_wick  = c["high"] - max(c["close"], c["open"])
        if body > 0 and lower_wick > 2 * body and upper_wick < 0.5 * body:
            score += 0.30; patterns.append("hammer")

        # Shooting star (bearish reversal)
        elif body > 0 and upper_wick > 2 * body and lower_wick < 0.5 * body:
            score -= 0.30; patterns.append("shooting_star")

        # Doji (indecision — reduce conviction)
        bar_range = c["high"] - c["low"]
        if bar_range > 0 and body / bar_range < 0.10:
            score *= 0.50; patterns.append("doji")

        # Inside bar (contraction)
        if c["high"] <= p["high"] and c["low"] >= p["low"]:
            score *= 0.70; patterns.append("inside_bar")

        # Higher highs / lower lows
        if len(candles) >= 3:
            h1, h2, h3 = candles[-3]["high"], candles[-2]["high"], candles[-1]["high"]
            l1, l2, l3 = candles[-3]["low"],  candles[-2]["low"],  candles[-1]["low"]
            if h3 > h2 > h1 and l3 > l2 > l1:
                score += 0.15; patterns.append("higher_highs")
            elif h3 < h2 < h1 and l3 < l2 < l1:
                score -= 0.15; patterns.append("lower_lows")

        # Morning/evening star (3-candle reversal)
        if len(candles) >= 3:
            a, b, mid = candles[-3], candles[-2], candles[-1]
            mid_body = abs(b["close"] - b["open"])
            mid_range = b["high"] - b["low"]
            if (mid_range > 0 and mid_body / mid_range < 0.3
                    and a["close"] < a["open"]
                    and mid["close"] > mid["open"]):
                score += 0.25; patterns.append("morning_star")
            elif (mid_range > 0 and mid_body / mid_range < 0.3
                  and a["close"] > a["open"]
                  and mid["close"] < mid["open"]):
                score -= 0.25; patterns.append("evening_star")

        score = max(-1.0, min(1.0, score))

        if score > 0.15:
            action = "BUY";  confidence = min(0.92, 0.50 + score * 0.50)
        elif score < -0.15:
            action = "SELL"; confidence = min(0.92, 0.50 + abs(score) * 0.50)
        else:
            action = "HOLD"; confidence = 0.50

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(confidence, 3),
            reasoning=", ".join(patterns) if patterns else "No clear pattern",
            indicators={"patterns": patterns, "score": round(score, 3)},
        )
