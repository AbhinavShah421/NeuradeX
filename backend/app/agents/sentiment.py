"""Sentiment Agent — LLM-based market context analysis with rule-based fallback."""
from __future__ import annotations
import asyncio
import json
import re
from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


class SentimentAgent(BaseAgent):
    name = "sentiment"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data")
        try:
            return await asyncio.wait_for(
                self._llm_sentiment(symbol, candles, context), timeout=8.0
            )
        except Exception as exc:
            logger.debug("Sentiment LLM skipped (%s) — using rule-based fallback", exc)
            return self._rule_based(candles)

    async def _llm_sentiment(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        import ollama
        from app.config import settings

        recent      = candles[-10:]
        price_chg   = (recent[-1]["close"] - recent[0]["close"]) / recent[0]["close"] * 100
        cur_price   = recent[-1]["close"]
        high_10     = max(c["high"] for c in recent)
        low_10      = min(c["low"]  for c in recent)
        position    = context.get("position", "NONE")

        prompt = f"""You are a concise quantitative trading analyst. Analyze {symbol}:

- 10-candle price change: {price_chg:+.2f}%
- Current price: {cur_price:.2f}
- 10-bar high/low: {high_10:.2f} / {low_10:.2f}
- Current position: {position}

Respond with EXACTLY this JSON (nothing else):
{{"action":"BUY","confidence":0.65,"reasoning":"brief reason under 15 words"}}

Rules: action must be BUY/SELL/HOLD, confidence 0.0–1.0."""

        model   = getattr(settings, "LLM_MODEL", "llama3.2")
        resp    = await asyncio.to_thread(
            lambda: ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1},
            )
        )
        content = resp.get("message", {}).get("content", "{}")
        match   = re.search(r"\{[^{}]+\}", content)
        if not match:
            raise ValueError("No JSON in LLM response")
        data = json.loads(match.group())

        action     = data.get("action", "HOLD").upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        confidence = float(max(0.10, min(0.95, data.get("confidence", 0.50))))

        return AgentSignal(
            agent_name=self.name, action=action, confidence=confidence,
            reasoning=str(data.get("reasoning", "LLM analysis")),
            indicators={"price_change_10c": round(price_chg, 3), "source": "llm"},
        )

    @staticmethod
    def _rule_based(candles: list[dict]) -> AgentSignal:
        """Fallback when LLM is unavailable — simple price-trend analysis."""
        closes = [c["close"] for c in candles[-10:]]
        pct    = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0

        bullish_bars = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
        bull_ratio   = bullish_bars / (len(closes) - 1) if len(closes) > 1 else 0.5

        if pct > 1.5 and bull_ratio > 0.6:
            return AgentSignal(agent_name="sentiment", action="BUY",
                               confidence=0.55, reasoning=f"Positive trend +{pct:.1f}%",
                               indicators={"price_change_10c": round(pct, 2), "source": "rule"})
        elif pct < -1.5 and bull_ratio < 0.4:
            return AgentSignal(agent_name="sentiment", action="SELL",
                               confidence=0.55, reasoning=f"Negative trend {pct:.1f}%",
                               indicators={"price_change_10c": round(pct, 2), "source": "rule"})
        return AgentSignal(agent_name="sentiment", action="HOLD",
                           confidence=0.50, reasoning="No clear sentiment signal",
                           indicators={"price_change_10c": round(pct, 2), "source": "rule"})
