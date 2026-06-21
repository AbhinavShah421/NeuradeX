"""Pattern Recognition Agent — candlestick patterns, trend direction.

v2 fixes:
  • Reversal patterns (hammer, shooting star, morning/evening star) now require
    trend context: a hammer only counts as bullish when it forms after a downtrend;
    a shooting star only counts as bearish after an uptrend. Without context they
    fired unconditionally and added noise regardless of the market structure.
  • Morning/evening star variable naming fixed: the 3 bars are now named
    (first, pivot, last) instead of (a, b, mid) where 'mid' misleadingly pointed
    to candles[-1] (the most recent bar, not the middle one).
  • Confidence cap lowered from 0.92 → 0.85 for consistency with other agents.
"""
from __future__ import annotations
from .base import AgentSignal, BaseAgent


def _trend_5(candles: list[dict]) -> float:
    """5-bar return as a fraction. Positive = recent uptrend, negative = downtrend."""
    if len(candles) < 6:
        return 0.0
    base = candles[-6]["close"]
    return (candles[-1]["close"] - base) / base if base > 0 else 0.0


class PatternAgent(BaseAgent):
    name = "pattern"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Not enough candles for pattern analysis")

        cur = candles[-1]   # current (latest) bar
        prv = candles[-2]   # previous bar

        score    = 0.0
        patterns: list[str] = []

        # ── Trend context (last 10 bars) ──────────────────────────────────────
        trend_10 = 0.0
        if len(candles) >= 10:
            w        = [x["close"] for x in candles[-10:]]
            trend_10 = (w[-1] - w[0]) / w[0] if w[0] > 0 else 0.0
            if trend_10 > 0.015:
                score += 0.20; patterns.append("uptrend")
            elif trend_10 < -0.015:
                score -= 0.20; patterns.append("downtrend")

        # 5-bar trend for reversal pattern context
        trend_5 = _trend_5(candles)
        in_downtrend = trend_5 < -0.005 or trend_10 < -0.005
        in_uptrend   = trend_5 >  0.005 or trend_10 >  0.005

        # ── Engulfing (continuation/reversal with directional body) ───────────
        if (prv["close"] < prv["open"] and cur["close"] > cur["open"]
                and cur["open"] < prv["close"] and cur["close"] > prv["open"]):
            score += 0.40; patterns.append("bullish_engulfing")
        elif (prv["close"] > prv["open"] and cur["close"] < cur["open"]
              and cur["open"] > prv["close"] and cur["close"] < prv["open"]):
            score -= 0.40; patterns.append("bearish_engulfing")

        # ── Hammer (bullish reversal) — only valid after a downtrend ─────────
        body       = abs(cur["close"] - cur["open"])
        lower_wick = min(cur["close"], cur["open"]) - cur["low"]
        upper_wick = cur["high"] - max(cur["close"], cur["open"])
        if body > 0 and lower_wick > 2 * body and upper_wick < 0.5 * body:
            if in_downtrend:
                score += 0.30; patterns.append("hammer")
            else:
                # Still a notable wick but not a reversal setup — lower weight
                score += 0.10; patterns.append("hammer_no_context")

        # ── Shooting star (bearish reversal) — only valid after an uptrend ───
        elif body > 0 and upper_wick > 2 * body and lower_wick < 0.5 * body:
            if in_uptrend:
                score -= 0.30; patterns.append("shooting_star")
            else:
                score -= 0.10; patterns.append("shooting_star_no_context")

        # ── Doji (indecision — reduce conviction) ─────────────────────────────
        bar_range = cur["high"] - cur["low"]
        if bar_range > 0 and body / bar_range < 0.10:
            score *= 0.50; patterns.append("doji")

        # ── Inside bar (contraction — reduce conviction) ───────────────────────
        if cur["high"] <= prv["high"] and cur["low"] >= prv["low"]:
            score *= 0.70; patterns.append("inside_bar")

        # ── Higher highs / lower lows ─────────────────────────────────────────
        if len(candles) >= 3:
            h1, h2, h3 = candles[-3]["high"], candles[-2]["high"], candles[-1]["high"]
            l1, l2, l3 = candles[-3]["low"],  candles[-2]["low"],  candles[-1]["low"]
            if h3 > h2 > h1 and l3 > l2 > l1:
                score += 0.15; patterns.append("higher_highs")
            elif h3 < h2 < h1 and l3 < l2 < l1:
                score -= 0.15; patterns.append("lower_lows")

        # ── Morning / evening star (3-candle reversal) ────────────────────────
        # first: the candle that starts the move (bar -3)
        # pivot: the indecision/gap bar in the middle (bar -2)
        # last:  the confirmation candle (bar -1, current)
        if len(candles) >= 3:
            first, pivot, last = candles[-3], candles[-2], candles[-1]
            pivot_body  = abs(pivot["close"] - pivot["open"])
            pivot_range = pivot["high"] - pivot["low"]
            pivot_is_indecision = pivot_range > 0 and pivot_body / pivot_range < 0.3
            if (pivot_is_indecision
                    and first["close"] < first["open"]   # first bar bearish
                    and last["close"]  > last["open"]    # last bar bullish
                    and in_downtrend):                   # reversal context
                score += 0.25; patterns.append("morning_star")
            elif (pivot_is_indecision
                  and first["close"] > first["open"]    # first bar bullish
                  and last["close"]  < last["open"]     # last bar bearish
                  and in_uptrend):                      # reversal context
                score -= 0.25; patterns.append("evening_star")

        score = max(-1.0, min(1.0, score))

        if score > 0.15:
            action = "BUY";  confidence = min(0.85, 0.50 + score * 0.45)
        elif score < -0.15:
            action = "SELL"; confidence = min(0.85, 0.50 + abs(score) * 0.45)
        else:
            action = "HOLD"; confidence = 0.50

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(confidence, 3),
            reasoning=", ".join(patterns) if patterns else "No clear pattern",
            indicators={"patterns": patterns, "score": round(score, 3),
                        "trend_5": round(trend_5, 4), "trend_10": round(trend_10, 4)},
        )
