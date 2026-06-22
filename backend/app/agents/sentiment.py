"""Sentiment Agent — the ensemble's one price-independent voice.

It reads the LLM news signal produced off the hot path by the sentiment-service
and only votes directionally (BUY/SELL) when there is a genuinely strong,
*catalyst-backed* news signal. Otherwise it ABSTAINS (HOLD).

Why the high bar: a weak local model (e.g. llama3.2) tends to rate almost every
stock vaguely "positive". If the agent passed that through as BUY it would
constantly nudge the ensemble to BUY and defeat the conviction gate (which only
enters when the ensemble actually agrees BUY) — i.e. overtrade and bleed. So
without a real catalyst the agent stays silent rather than guessing from price.
"""
from __future__ import annotations
import asyncio
import json
import os
from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# Bars a news signal must clear to be allowed to move the ensemble.
NEWS_MIN_CONFIDENCE = float(os.getenv("NEWS_MIN_CONFIDENCE", "0.7"))
NEWS_MIN_SCORE      = float(os.getenv("NEWS_MIN_SCORE", "0.4"))   # |score|, score is -1..1
_VAGUE_CATALYSTS = {"", "none", "n/a", "na", "no catalyst", "no specific catalyst",
                    "no clear catalyst", "mixed", "unclear", "-"}


class SentimentAgent(BaseAgent):
    name = "sentiment"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data")
        # In backtest/replay mode use the date-specific cached signal so the ensemble
        # reflects what the market knew on that day, not today's news.
        date = None
        if context.get("mode") in ("backtest", "replay"):
            date = context.get("date")
        return await self._news_signal(symbol, date=date)

    async def _news_signal(self, symbol: str, date: str | None = None) -> AgentSignal:
        abstain = AgentSignal(
            agent_name=self.name, action="HOLD", confidence=0.4,
            reasoning="No strong news catalyst — abstaining",
            indicators={"source": "news_llm", "status": "abstain"},
        )
        try:
            from app.utils.redis_client import cache_get
            sym = symbol.upper()
            # Use date-specific key for backtest/replay; live key for paper/live.
            if date:
                redis_key = f"ai_engine:sentiment:{sym}:{date}"
                raw = await cache_get(redis_key)
                if not raw:
                    # Historical data not pre-fetched yet — kick off a background
                    # fetch so the NEXT candle has a real signal.
                    try:
                        from app.agents.sentiment_pipeline import run_pipeline_for_date
                        asyncio.create_task(run_pipeline_for_date(sym, date))
                    except Exception:
                        pass
                    return abstain
            else:
                raw = await cache_get(f"ai_engine:sentiment:{sym}")
                if not raw:
                    # No data yet — kick off the pipeline in the background so the
                    # NEXT ensemble call has a real signal to vote on.
                    try:
                        from app.agents.sentiment_pipeline import run_pipeline
                        asyncio.create_task(run_pipeline(sym))
                    except Exception:
                        pass
                    return abstain
            d = json.loads(raw)
        except Exception as exc:
            logger.debug("news sentiment read failed for %s: %s", symbol, exc)
            return abstain

        if int(d.get("headlines_count", 0)) <= 0:
            return abstain

        sentiment = str(d.get("sentiment", "neutral")).lower()
        try:
            score = abs(float(d.get("score", 0) or 0))
            conf  = float(d.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            return abstain
        catalyst = str(d.get("catalyst", "") or "").strip().lower()
        has_catalyst = catalyst not in _VAGUE_CATALYSTS

        # Vote only on a genuinely strong, directional, catalyst-backed signal.
        if (sentiment in ("positive", "negative")
                and conf >= NEWS_MIN_CONFIDENCE
                and score >= NEWS_MIN_SCORE
                and has_catalyst):
            action = "BUY" if sentiment == "positive" else "SELL"
            return AgentSignal(
                agent_name=self.name, action=action, confidence=min(0.90, conf),
                reasoning=str(d.get("summary") or d.get("catalyst") or "News catalyst")[:160],
                indicators={
                    "source": "news_llm", "sentiment": sentiment,
                    "score": d.get("score"), "catalyst": d.get("catalyst"),
                    "headlines": d.get("headlines_count"), "provider": d.get("provider"),
                    "date": d.get("date"),  # present for historical signals
                },
            )
        return abstain
