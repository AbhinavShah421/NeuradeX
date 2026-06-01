"""Sentiment Agent — reads the LLM news-sentiment signal (produced off the hot
path by the sentiment-service from Google-News headlines), with a rule-based
price fallback. This is the one agent driven by information that is genuinely
independent of price, which is what gives the ensemble real diversity."""
from __future__ import annotations
import json
from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


class SentimentAgent(BaseAgent):
    name = "sentiment"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data")
        # 1) News-driven LLM sentiment, cached in Redis by the sentiment-service.
        sig = await self._cached_news_signal(symbol)
        if sig is not None:
            return sig
        # 2) Fallback (cold start / off-watchlist): rule-based price trend.
        return self._rule_based(candles)

    async def _cached_news_signal(self, symbol: str) -> AgentSignal | None:
        try:
            from app.utils.redis_client import cache_get
            raw = await cache_get(f"ai_engine:sentiment:{symbol.upper()}")
            if not raw:
                return None
            d = json.loads(raw)
        except Exception as exc:
            logger.debug("news sentiment read failed for %s: %s", symbol, exc)
            return None

        # If the worker found no news, don't override the price fallback.
        if int(d.get("headlines_count", 0)) <= 0:
            return None

        action = str(d.get("action", "HOLD")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        confidence = float(max(0.10, min(0.95, d.get("confidence", 0.50))))
        return AgentSignal(
            agent_name=self.name, action=action, confidence=confidence,
            reasoning=str(d.get("summary", "News sentiment"))[:160],
            indicators={
                "source": "news_llm",
                "sentiment": d.get("sentiment"),
                "score": d.get("score"),
                "catalyst": d.get("catalyst"),
                "headlines": d.get("headlines_count"),
                "provider": d.get("provider"),
            },
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
