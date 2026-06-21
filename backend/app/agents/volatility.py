"""Volatility / Risk Monitor — ATR percentile, Bollinger squeeze breakout, vol trend.

v2 revamp — root-cause of 38% accuracy in v1:
  • v1 mapped score ±0.10-0.12 → confidence 0.80-0.86 via (0.50 + score*3).
    That is catastrophically overconfident for near-random 5-bar signals.
  • v1 used fixed ATR thresholds (1.5% / 3.0%) which are meaningless across
    symbols with different price levels and typical ranges.
  • v1 tried to predict direction from volatility level alone — there is no
    such edge. The directional signal was pure noise voted at 80%+ confidence.

v2 design:
  • Risk score uses ATR *percentile* over the rolling window so it adapts to
    each symbol's own volatility distribution.
  • Directional signal fires ONLY on a Bollinger-Band squeeze breakout:
    BB width must be in the bottom 25th percentile of its own recent history
    for ≥ 3 consecutive bars, and the latest bar closes outside the band.
    This is the one vol-based edge with documented statistical backing.
  • Always HOLD (but set risk_score high) during high-vol (top 30th ATR
    percentile) — let better-positioned agents decide direction, the ensemble
    uses risk_score to size down automatically.
  • Confidence capped at 0.65 — this is a risk lens, not a direction oracle.
  • vol_trend indicator added: "expanding" / "contracting" / "stable" tells
    the ensemble whether conditions are worsening or improving.
"""
from __future__ import annotations
import math
from .base import AgentSignal, BaseAgent

_MIN_BARS      = 30   # need enough history for percentile to be meaningful
_ATR_PERIOD    = 14
_BB_PERIOD     = 20
_BB_STD        = 2.0
_SQUEEZE_PCTILE = 25  # BB width below this percentile = squeeze
_SQUEEZE_BARS  = 3    # must be in squeeze for this many consecutive bars
_HIGH_VOL_PCTILE = 70 # ATR above this percentile = high-vol HOLD zone
_MAX_CONF      = 0.65


class VolatilityAgent(BaseAgent):
    name = "volatility"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < _MIN_BARS:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.40,
                reasoning="Insufficient history for volatility percentile",
                indicators={"risk_score": 0.50},
            )

        closes = [c["close"] for c in candles]
        n      = len(closes)

        # ── ATR series (one value per bar, starting at bar 1) ────────────────
        atrs: list[float] = []
        trs:  list[float] = []
        for i in range(1, n):
            prev = closes[i - 1]
            h    = candles[i].get("high") or closes[i]
            l    = candles[i].get("low")  or closes[i]
            trs.append(max(h - l, abs(h - prev), abs(l - prev)))
            if len(trs) >= _ATR_PERIOD:
                atrs.append(sum(trs[-_ATR_PERIOD:]) / _ATR_PERIOD)

        if not atrs:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.40,
                reasoning="ATR series too short",
                indicators={"risk_score": 0.50},
            )

        current_atr     = atrs[-1]
        atr_pct         = current_atr / closes[-1] * 100 if closes[-1] > 0 else 0.0
        atr_percentile  = _percentile_rank(atrs, current_atr)

        # ── Bollinger Band width series ───────────────────────────────────────
        bb_widths: list[float] = []
        for i in range(_BB_PERIOD - 1, n):
            window = closes[i - _BB_PERIOD + 1 : i + 1]
            mu     = sum(window) / _BB_PERIOD
            sigma  = math.sqrt(sum((x - mu) ** 2 for x in window) / _BB_PERIOD)
            bb_widths.append(sigma / mu * 100 if mu > 0 else 0.0)

        current_bb_width = bb_widths[-1] if bb_widths else 0.0
        bb_upper, bb_lower = _bollinger_bands(closes, _BB_PERIOD, _BB_STD)

        # ── Risk score (0 = safe, 1 = dangerous) ─────────────────────────────
        # Blend ATR percentile (70%) with BB width percentile (30%)
        bb_pctile  = _percentile_rank(bb_widths, current_bb_width) if bb_widths else 50.0
        risk_score = round((0.70 * atr_percentile + 0.30 * bb_pctile) / 100.0, 3)

        # ── Volatility trend ──────────────────────────────────────────────────
        if len(atrs) >= 5:
            atr_slope = (atrs[-1] - atrs[-5]) / max(atrs[-5], 1e-9)
            vol_trend = "expanding" if atr_slope > 0.10 else "contracting" if atr_slope < -0.10 else "stable"
        else:
            vol_trend = "stable"

        signals: dict = {
            "atr":           round(current_atr, 3),
            "atr_pct":       round(atr_pct, 3),
            "atr_percentile": round(atr_percentile, 1),
            "bb_width_pct":  round(current_bb_width, 3),
            "bb_pctile":     round(bb_pctile, 1),
            "risk_score":    risk_score,
            "vol_trend":     vol_trend,
        }

        # ── High-vol zone: always HOLD, just pass risk score ─────────────────
        if atr_percentile >= _HIGH_VOL_PCTILE:
            signals["regime"] = "high_volatility"
            return AgentSignal(
                agent_name=self.name, action="HOLD",
                confidence=0.45,
                reasoning=f"High-vol zone (ATR p{atr_percentile:.0f}) — abstaining from direction",
                indicators=signals,
            )

        # ── Bollinger Band squeeze → breakout detection ───────────────────────
        if len(bb_widths) >= _SQUEEZE_BARS + 1:
            squeeze_threshold = _percentile_value(bb_widths[:-1], _SQUEEZE_PCTILE)
            in_squeeze = all(
                w <= squeeze_threshold
                for w in bb_widths[-(1 + _SQUEEZE_BARS): -1]
            )
        else:
            in_squeeze = False

        if in_squeeze and bb_upper is not None and bb_lower is not None:
            price = closes[-1]
            if price > bb_upper:
                signals["regime"]  = "squeeze_breakout_up"
                signals["squeeze"] = True
                return AgentSignal(
                    agent_name=self.name, action="BUY",
                    confidence=min(_MAX_CONF, 0.55 + (1 - risk_score) * 0.10),
                    reasoning=f"BB squeeze breakout ↑ (w p{bb_pctile:.0f}, ATR p{atr_percentile:.0f})",
                    indicators=signals,
                )
            if price < bb_lower:
                signals["regime"]  = "squeeze_breakout_down"
                signals["squeeze"] = True
                return AgentSignal(
                    agent_name=self.name, action="SELL",
                    confidence=min(_MAX_CONF, 0.55 + (1 - risk_score) * 0.10),
                    reasoning=f"BB squeeze breakout ↓ (w p{bb_pctile:.0f}, ATR p{atr_percentile:.0f})",
                    indicators=signals,
                )

        # ── Default: HOLD with calibrated confidence ──────────────────────────
        # Confidence is inversely related to risk — in calm markets we are more
        # certain there is no edge from volatility, so HOLD is a committed signal.
        signals["regime"] = "low_volatility" if atr_percentile < 30 else "moderate_volatility"
        hold_conf = round(0.50 + (1.0 - risk_score) * 0.12, 3)
        return AgentSignal(
            agent_name=self.name, action="HOLD",
            confidence=min(_MAX_CONF, hold_conf),
            reasoning=f"{signals['regime']}, ATR p{atr_percentile:.0f}, risk {risk_score:.2f}, vol {vol_trend}",
            indicators=signals,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _percentile_rank(series: list[float], value: float) -> float:
    """Return what percentile `value` falls at in `series` (0–100)."""
    if not series:
        return 50.0
    below = sum(1 for x in series if x < value)
    return below / len(series) * 100.0


def _percentile_value(series: list[float], pctile: float) -> float:
    """Return the value at the given percentile (0–100) in `series`."""
    if not series:
        return 0.0
    s   = sorted(series)
    idx = int(len(s) * pctile / 100.0)
    return s[min(idx, len(s) - 1)]


def _bollinger_bands(
    closes: list[float], period: int, n_std: float
) -> tuple[float | None, float | None]:
    if len(closes) < period:
        return None, None
    window = closes[-period:]
    mu     = sum(window) / period
    sigma  = math.sqrt(sum((x - mu) ** 2 for x in window) / period)
    return mu + n_std * sigma, mu - n_std * sigma
