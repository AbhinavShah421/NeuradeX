"""Technical Analysis Agent — RSI, MACD, Bollinger Bands, VWAP, SMA crossover."""
from __future__ import annotations
import math
from .base import AgentSignal, BaseAgent


class TechnicalAgent(BaseAgent):
    name = "technical"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 20:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for technical analysis")

        closes  = [c["close"] for c in candles]
        idx     = len(closes) - 1

        rsi                         = self._rsi(closes, 14)
        macd_line, sig_line, hist   = self._macd(closes)
        bb_upper, _, bb_lower, bb_pct = self._bollinger(closes, 20, 2.0)
        vwap                        = self._vwap(candles)
        sma5  = sum(closes[max(0, idx - 4): idx + 1]) / min(5,  idx + 1)
        sma20 = sum(closes[max(0, idx - 19): idx + 1]) / min(20, idx + 1)
        price = closes[idx]

        score   = 0.0
        signals = {}

        # RSI
        if rsi < 30:
            score += 0.30; signals["rsi"] = "oversold"
        elif rsi > 70:
            score -= 0.30; signals["rsi"] = "overbought"
        elif rsi < 45:
            score += 0.10; signals["rsi"] = "soft_oversold"
        elif rsi > 55:
            score -= 0.10; signals["rsi"] = "soft_overbought"
        else:
            signals["rsi"] = "neutral"

        # MACD
        if macd_line > sig_line and hist > 0:
            score += 0.25; signals["macd"] = "bullish_cross"
        elif macd_line < sig_line and hist < 0:
            score -= 0.25; signals["macd"] = "bearish_cross"
        elif hist > 0:
            score += 0.10; signals["macd"] = "rising"
        else:
            score -= 0.10; signals["macd"] = "falling"

        # Bollinger Bands
        if bb_pct < 0.20:
            score += 0.20; signals["bb"] = "near_lower"
        elif bb_pct > 0.80:
            score -= 0.20; signals["bb"] = "near_upper"
        elif bb_pct < 0.40:
            score += 0.05
        else:
            score -= 0.05

        # VWAP
        if vwap and price > vwap:
            score += 0.15; signals["vwap"] = "above"
        elif vwap and price < vwap:
            score -= 0.15; signals["vwap"] = "below"
        else:
            signals["vwap"] = "at"

        # SMA crossover
        if sma5 > sma20:
            score += 0.10; signals["sma"] = "golden_cross"
        else:
            score -= 0.10; signals["sma"] = "death_cross"

        score = max(-1.0, min(1.0, score))

        if score > 0.20:
            action = "BUY";  confidence = min(0.95, 0.50 + score * 0.50)
        elif score < -0.20:
            action = "SELL"; confidence = min(0.95, 0.50 + abs(score) * 0.50)
        else:
            action = "HOLD"; confidence = 0.50 + (0.20 - abs(score)) * 0.50

        reasons = []
        if "rsi" in signals and signals["rsi"] in ("oversold", "overbought"):
            reasons.append(f"RSI {rsi:.1f} ({signals['rsi']})")
        if signals.get("macd") in ("bullish_cross", "bearish_cross"):
            reasons.append(f"MACD {signals['macd']}")
        if signals.get("vwap") in ("above", "below"):
            reasons.append(f"Price {signals['vwap']} VWAP ₹{vwap:.2f}" if vwap else "")
        if signals.get("sma") in ("golden_cross", "death_cross"):
            reasons.append(f"SMA {signals['sma']}")

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(confidence, 3),
            reasoning="; ".join(r for r in reasons if r) or f"Score {score:.2f}",
            indicators={
                "rsi":       round(rsi, 2),
                "macd":      round(macd_line, 4),
                "macd_sig":  round(sig_line, 4),
                "macd_hist": round(hist, 4),
                "bb_pct":    round(bb_pct, 3),
                "bb_upper":  round(bb_upper, 2),
                "bb_lower":  round(bb_lower, 2),
                "vwap":      round(vwap, 2) if vwap else None,
                "sma5":      round(sma5, 2),
                "sma20":     round(sma20, 2),
                "score":     round(score, 3),
                **signals,
            },
        )

    # ── Indicator helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_g  = sum(gains[:period]) / period
        avg_l  = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        return 100.0 if avg_l == 0 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))

    @staticmethod
    def _ema(closes: list[float], period: int) -> list[float]:
        k   = 2 / (period + 1)
        ema = [closes[0]]
        for c in closes[1:]:
            ema.append(c * k + ema[-1] * (1 - k))
        return ema

    def _macd(self, closes: list[float]) -> tuple[float, float, float]:
        if len(closes) < 26:
            return 0.0, 0.0, 0.0
        ema12  = self._ema(closes, 12)
        ema26  = self._ema(closes, 26)
        series = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        signal = self._ema(series, 9) if len(series) >= 9 else series
        m = series[-1]; s = signal[-1]
        return m, s, m - s

    @staticmethod
    def _bollinger(closes: list[float], period: int, std_dev: float) -> tuple[float, float, float, float]:
        if len(closes) < period:
            return closes[-1], closes[-1], closes[-1], 0.5
        w   = closes[-period:]
        mid = sum(w) / period
        std = math.sqrt(sum((c - mid) ** 2 for c in w) / period)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        pct   = (closes[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        return upper, mid, lower, pct

    @staticmethod
    def _vwap(candles: list[dict]) -> float:
        tv = sum((c["high"] + c["low"] + c["close"]) / 3 * c.get("volume", 1) for c in candles)
        v  = sum(c.get("volume", 1) for c in candles)
        return tv / v if v > 0 else sum(c["close"] for c in candles) / len(candles)
