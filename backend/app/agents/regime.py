"""Market-Regime Filter model — classifies the *kind* of market, then tilts the
ensemble toward the models that work in it.

It blends trend strength (ADX), price-vs-moving-average structure, and realized
volatility into one of four regimes:
  • trend    — strong directional move; trust momentum/trend, fade mean-reversion
  • chop     — directionless; trust mean-reversion, distrust momentum
  • range    — quiet sideways; like chop but calmer
  • high_vol — wide, erratic bars; damp every directional voice

The ensemble reads `regime` from this agent's indicators and reweights the other
models accordingly (see EnsembleEngine._apply_regime). The agent also casts a
light vote in the trend's direction so a clean trend gets a nudge.
"""
from __future__ import annotations
import math

from .base import AgentSignal, BaseAgent


class RegimeFilterAgent(BaseAgent):
    name = "regime"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        closes = [c["close"] for c in candles if c.get("close")]
        highs = [c.get("high", c["close"]) for c in candles if c.get("close")]
        lows = [c.get("low", c["close"]) for c in candles if c.get("close")]
        if len(closes) < 30:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for regime classification",
                               indicators={"regime": "unknown"})

        idx = len(closes) - 1
        price = closes[idx]
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
        adx = self._adx(highs, lows, closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / price * 100) if price else 0.0

        # Normalized slope of the 20-SMA over the last 10 bars (% per bar).
        if len(closes) >= 30:
            prev_sma20 = sum(closes[-30:-10]) / 20
            slope_pct = (sma20 - prev_sma20) / prev_sma20 * 100 if prev_sma20 else 0.0
        else:
            slope_pct = 0.0

        ma_aligned = (price > sma20 > sma50) or (price < sma20 < sma50)

        # ── Classify ────────────────────────────────────────────────────────────
        if atr_pct >= 4.0:
            regime = "high_vol"
        elif adx >= 25 and ma_aligned and abs(slope_pct) >= 0.15:
            regime = "trend"
        elif adx < 18 and atr_pct < 1.5:
            regime = "range"
        else:
            regime = "chop"

        sig = {"regime": regime, "adx": round(adx, 1), "atr_pct": round(atr_pct, 2),
               "slope_pct": round(slope_pct, 3), "ma_aligned": ma_aligned}

        # Light directional vote only when clearly trending.
        if regime == "trend" and price > sma20:
            action, conf = "BUY", min(0.8, 0.5 + adx / 100)
            reason = f"Trend regime (ADX {adx:.0f}, rising) — momentum favoured."
        elif regime == "trend" and price < sma20:
            action, conf = "SELL", min(0.8, 0.5 + adx / 100)
            reason = f"Trend regime (ADX {adx:.0f}, falling) — momentum favoured."
        else:
            action, conf = "HOLD", 0.45
            reason = f"{regime.replace('_', ' ').title()} regime (ADX {adx:.0f}, ATR {atr_pct:.1f}%)."

        return AgentSignal(agent_name=self.name, action=action, confidence=round(conf, 3),
                           reasoning=reason, indicators=sig)

    @staticmethod
    def _atr(highs, lows, closes, period=14) -> float:
        if len(closes) < 2:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        trs = trs[-period:]
        return sum(trs) / len(trs) if trs else 0.0

    @staticmethod
    def _adx(highs, lows, closes, period=14) -> float:
        n = len(closes)
        if n < period + 2:
            return 0.0
        plus_dm, minus_dm, trs = [], [], []
        for i in range(1, n):
            up = highs[i] - highs[i - 1]
            dn = lows[i - 1] - lows[i]
            plus_dm.append(up if (up > dn and up > 0) else 0.0)
            minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        plus_dm, minus_dm, trs = plus_dm[-period:], minus_dm[-period:], trs[-period:]
        atr = sum(trs)
        if atr <= 1e-9:
            return 0.0
        pdi = 100 * sum(plus_dm) / atr
        mdi = 100 * sum(minus_dm) / atr
        denom = pdi + mdi
        if denom <= 1e-9:
            return 0.0
        dx = 100 * abs(pdi - mdi) / denom
        return dx  # single-period DX as a light-weight ADX proxy
