"""Day Structure Agent — intraday price-level awareness.

Reads the FULL day's candle history to answer the one question every human
trader considers before entering: where in today's structure are we, and is
the risk/reward favorable right now?

Signals:
  BUY   — near day low / confirmed swing support, good R/R (upside >> downside)
  SELL  — near day high / confirmed swing resistance, poor R/R for a long
  HOLD  — mid-range or insufficient data to form a view

Key indicators published (readable by other agents via context in future):
  day_range_pct    : 0.0 (day low) → 1.0 (day high) — WHERE we are today
  dist_resistance  : % gap to nearest overhead resistance
  dist_support     : % gap to nearest floor support
  rr_ratio         : upside/downside ratio  (>1 = favourable for long)
  morning_bias     : "above" | "below" | "inside" the morning range
  extended_move    : True if price travelled >2% from open AND is in top/bottom 20%
"""
from __future__ import annotations
from .base import AgentSignal, BaseAgent

# Minimum candles before we have enough day structure to form a view.
# 30 candles = 30 minutes of 1-min data; shorter sessions return HOLD.
_MIN_CANDLES   = 30
# Window for swing-point detection (bars on each side that must be lower/higher).
_SWING_WINDOW  = 4
# Morning range: first N candles (≈ first 45 min at 1-min resolution).
_MORNING_BARS  = 45
# Thresholds
_NEAR_LEVEL_PCT  = 0.35   # within 0.35% of a key level = "touching it"
_EXTENDED_MOVE   = 2.0    # % move from day open counts as extended


class DayStructureAgent(BaseAgent):
    name = "day_structure"

    async def analyze(
        self, symbol: str, candles: list[dict], context: dict
    ) -> AgentSignal:
        if len(candles) < _MIN_CANDLES:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.50,
                reasoning="Building day structure — need 30+ candles",
            )

        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]

        price     = closes[-1]
        day_open  = candles[0]["open"]
        day_high  = max(highs)
        day_low   = min(lows)
        day_range = day_high - day_low or 0.01

        # Where price sits in today's full range: 0 = day low, 1 = day high
        day_range_pct = (price - day_low) / day_range

        # ── Morning range (first 45 min or 25% of candles, whichever smaller) ─
        m_end        = min(_MORNING_BARS, max(10, len(candles) // 4))
        morning_high = max(highs[:m_end])
        morning_low  = min(lows[:m_end])
        if price > morning_high:
            morning_bias = "above"
        elif price < morning_low:
            morning_bias = "below"
        else:
            morning_bias = "inside"

        # ── Swing S/R detection ───────────────────────────────────────────────
        sw_highs, sw_lows = self._find_swings(candles, _SWING_WINDOW)

        # Prefer the SHARED clustered level map (ensemble injects it — the same
        # full-day structure every expert sees). Clustered levels carry touch
        # counts, so "resistance" here means a TESTED ceiling, not one stray
        # pivot. Fall back to raw swings when running outside the ensemble.
        lv = (context or {}).get("levels") or {}
        lv_res = [l for l in lv.get("resistances", []) if not l.get("at_price")]
        lv_sup = [l for l in lv.get("supports", []) if not l.get("at_price")]
        res_touches = sup_touches = 1
        if lv.get("ok") and (lv_res or lv_sup):
            if lv_res:
                nearest_res = lv_res[0]["price"]
                res_touches = int(lv_res[0].get("touches", 1))
            else:
                nearest_res = day_high
            if lv_sup:
                nearest_sup = lv_sup[0]["price"]
                sup_touches = int(lv_sup[0].get("touches", 1))
            else:
                nearest_sup = day_low
        else:
            resistances = [h for h in sw_highs if h > price * 1.001]
            nearest_res = min(resistances) if resistances else day_high
            supports    = [l for l in sw_lows if l < price * 0.999]
            nearest_sup = max(supports) if supports else day_low

        dist_res = (nearest_res - price) / price * 100   # % above price
        dist_sup = (price - nearest_sup) / price * 100   # % below price

        # Risk/reward for a long entry: upside to resistance / downside to support.
        # >1 = favourable; <0.5 = very risky.
        rr = dist_res / dist_sup if dist_sup > 0 else 0.5

        # ── Extended move ─────────────────────────────────────────────────────
        move_from_open = (price - day_open) / day_open * 100
        extended_up    = move_from_open >  _EXTENDED_MOVE and day_range_pct > 0.70
        extended_down  = move_from_open < -_EXTENDED_MOVE and day_range_pct < 0.30

        # ── Score ─────────────────────────────────────────────────────────────
        score    = 0.0
        evidence = []

        # Primary: position in day range. Thresholds tightened (0.72/0.58) so the
        # afternoon-chop-near-the-day-high entries — which lose on mean reversion —
        # are flagged, not just the extreme top.
        if day_range_pct > 0.72:
            score -= 0.55; evidence.append(f"top {(1-day_range_pct)*100:.0f}% of day range")
        elif day_range_pct > 0.58:
            score -= 0.34; evidence.append(f"upper third of day range")
        elif day_range_pct < 0.18:
            score += 0.55; evidence.append(f"bottom {day_range_pct*100:.0f}% of day range")
        elif day_range_pct < 0.32:
            score += 0.28; evidence.append(f"lower third of day range")
        else:
            evidence.append(f"mid-range ({day_range_pct*100:.0f}%)")

        # Risk/reward for a long — penalise poor R/R harder (a long with little room
        # to resistance and the full day below it is negative expectancy after costs).
        if rr < 0.50:
            score -= 0.38; evidence.append(f"R/R {rr:.1f}× (resistance too close)")
        elif rr < 0.90:
            score -= 0.18; evidence.append(f"R/R {rr:.1f}× (limited upside)")
        elif rr > 2.5:
            score += 0.22; evidence.append(f"R/R {rr:.1f}× (room to run)")
        elif rr > 1.5:
            score += 0.10

        # Touching a key level — a level tested multiple times carries more
        # weight than a single stray pivot (touch counts from the shared map).
        if dist_res < _NEAR_LEVEL_PCT:
            pen = 0.25 + 0.08 * min(3, res_touches - 1)
            score -= pen
            evidence.append(f"at {res_touches}x-tested resistance ₹{nearest_res:.2f} ({dist_res:.2f}% away)")
        if dist_sup < _NEAR_LEVEL_PCT and not (dist_res < _NEAR_LEVEL_PCT):
            bonus = 0.22 + 0.07 * min(3, sup_touches - 1)
            score += bonus
            evidence.append(f"at {sup_touches}x-tested support ₹{nearest_sup:.2f} ({dist_sup:.2f}% away)")

        # Morning range context
        if morning_bias == "above":
            score += 0.12; evidence.append("above morning range (bullish structure)")
        elif morning_bias == "below":
            score -= 0.12; evidence.append("below morning range (bearish structure)")

        # Extended-move penalty / bonus
        if extended_up:
            score -= 0.28; evidence.append(f"extended up {move_from_open:+.1f}% from open — chase risk")
        elif extended_down:
            score += 0.20; evidence.append(f"extended down {move_from_open:+.1f}% from open — mean-revert potential")

        score = max(-1.0, min(1.0, score))

        if score > 0.22:
            action     = "BUY"
            confidence = min(0.88, 0.50 + score * 0.48)
        elif score < -0.22:
            action     = "SELL"
            confidence = min(0.88, 0.50 + abs(score) * 0.48)
        else:
            action     = "HOLD"
            confidence = 0.55 - abs(score) * 0.15   # more neutral → more confident hold

        reasoning = (
            f"{'·'.join(evidence[:3])} | "
            f"range {day_range_pct*100:.0f}% | R/R {rr:.1f}× | "
            f"res ₹{nearest_res:.2f} ({dist_res:.2f}% up) sup ₹{nearest_sup:.2f} ({dist_sup:.2f}% down)"
        )

        return AgentSignal(
            agent_name=self.name,
            action=action,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            indicators={
                "day_range_pct":    round(day_range_pct, 3),
                "day_high":         round(day_high, 2),
                "day_low":          round(day_low, 2),
                "day_open":         round(day_open, 2),
                "nearest_res":      round(nearest_res, 2),
                "nearest_sup":      round(nearest_sup, 2),
                "dist_resistance":  round(dist_res, 3),
                "dist_support":     round(dist_sup, 3),
                "rr_ratio":         round(rr, 2),
                "morning_bias":     morning_bias,
                "morning_high":     round(morning_high, 2),
                "morning_low":      round(morning_low, 2),
                "extended_move":    extended_up or extended_down,
                "move_from_open":   round(move_from_open, 3),
                "swing_highs_n":    len(sw_highs),
                "swing_lows_n":     len(sw_lows),
                "res_touches":      res_touches,
                "sup_touches":      sup_touches,
                # Top shared levels (nearest first) — for the UI and the gate.
                "levels_res":       [{"price": l["price"], "touches": l["touches"],
                                      "dist_pct": l["dist_pct"]} for l in lv_res[:3]],
                "levels_sup":       [{"price": l["price"], "touches": l["touches"],
                                      "dist_pct": l["dist_pct"]} for l in lv_sup[:3]],
                "score":            round(score, 3),
            },
        )

    @staticmethod
    def _find_swings(
        candles: list[dict], window: int
    ) -> tuple[list[float], list[float]]:
        """Pivot-point swing detection.
        A swing high at i: candles[i].high is the local max over ±window bars.
        A swing low  at i: candles[i].low  is the local min over ±window bars.
        Only interior points are checked (avoids boundary artefacts).
        """
        n = len(candles)
        sw_highs: list[float] = []
        sw_lows:  list[float] = []

        for i in range(window, n - window):
            h = candles[i]["high"]
            l = candles[i]["low"]

            neighbours_h = [candles[j]["high"] for j in range(i - window, i + window + 1) if j != i]
            neighbours_l = [candles[j]["low"]  for j in range(i - window, i + window + 1) if j != i]

            if h >= max(neighbours_h):
                sw_highs.append(h)
            if l <= min(neighbours_l):
                sw_lows.append(l)

        return sw_highs, sw_lows
