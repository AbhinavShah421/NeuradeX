"""News-sentiment worker.

For each AI-watchlist stock it pulls recent Google-News headlines, asks the LLM
to judge the short-term sentiment, and caches the result in Redis at
`ai_engine:sentiment:{SYMBOL}`. The backend's sentiment agent reads that cache —
so the ensemble gets a signal driven by *news*, genuinely independent of price.

This runs entirely off the trading hot path (every SENTIMENT_INTERVAL), so the
per-candle ensemble stays fast.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import redis.asyncio as redis

from . import llm
from .news import fetch_headlines, headlines_block, now_iso

logger = logging.getLogger("sentiment-service")
IST = timezone(timedelta(hours=5, minutes=30))

WATCHLIST_KEY  = "ai_engine:watchlist"
CANDIDATES_KEY = "ai_engine:scan_candidates"   # broader pool the scanner ranks over
def _sentiment_key(sym: str) -> str:
    return f"ai_engine:sentiment:{sym.upper()}"

SENTIMENT_INTERVAL = int(os.getenv("SENTIMENT_INTERVAL", str(30 * 60)))   # full refresh cadence
SENTIMENT_TTL      = int(os.getenv("SENTIMENT_TTL", str(90 * 60)))        # how long a signal stays valid
FETCH_DELAY        = float(os.getenv("SENTIMENT_FETCH_DELAY", "1.0"))     # be gentle on news + LLM
MAX_HEADLINES      = int(os.getenv("SENTIMENT_MAX_HEADLINES", "8"))

_redis: redis.Redis | None = None
_state = {"last_run": None, "analyzed": 0, "with_news": 0, "provider": llm.resolve_provider(),
          "model": llm.active_model(), "running": False}


async def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0"
        _redis = await redis.from_url(url, encoding="utf8", decode_responses=True)
    return _redis


async def _watchlist() -> list[dict]:
    """Symbols to analyse: prefer the scanner's broader candidate pool (so a fresh
    catalyst can pull a stock *into* the watchlist), falling back to the live
    watchlist."""
    try:
        r = await _get_redis()
        raw = await r.get(CANDIDATES_KEY)
        if raw:
            items = json.loads(raw).get("items", [])
            if items:
                return items
        raw = await r.get(WATCHLIST_KEY)
        if raw:
            return json.loads(raw).get("items", [])
    except Exception:
        pass
    return []


_PROMPT = """You are a hard-to-impress financial news analyst. Judge the SHORT-TERM \
(intraday to 3-day) sentiment for the NSE stock {name} ({symbol}) from ONLY these headlines:

{headlines}

DEFAULT TO NEUTRAL. Most days a stock has no actionable news. Only return
positive/negative when there is a SPECIFIC, MATERIAL, recent catalyst —
e.g. earnings beat/miss, guidance change, a large order/contract, regulatory
action, M&A, management change, analyst upgrade/downgrade with a clear reason.
Generic "share price", "stock to watch", "technical chart", listing pages or
vague commentary are NOT catalysts → neutral, catalyst "none".

Respond with EXACTLY this JSON and nothing else:
{{"sentiment":"positive|negative|neutral","score":0.0,"action":"BUY|SELL|HOLD","confidence":0.0,"catalyst":"specific catalyst or none","summary":"one line under 16 words"}}

Rules:
- score: -1.0 (very bearish) to 1.0 (very bullish); near 0 when neutral.
- confidence: 0.0-1.0. Only exceed 0.7 when a concrete catalyst clearly drives it.
- If there is no specific catalyst, return sentiment "neutral", action "HOLD",
  score ~0, low confidence, catalyst "none".
- Base it on the news only, never guess."""


async def analyze_symbol(symbol: str, name: str) -> dict:
    headlines = await fetch_headlines(symbol, name, limit=MAX_HEADLINES)
    base = {
        "symbol": symbol.upper(), "name": name,
        "sentiment": "neutral", "score": 0.0, "action": "HOLD", "confidence": 0.3,
        "catalyst": "none", "summary": "No fresh news.", "headlines_count": len(headlines),
        "provider": llm.resolve_provider(), "model": llm.active_model(),
        "updated_at": now_iso(),
    }
    if not headlines:
        return base

    prompt = _PROMPT.format(name=name or symbol, symbol=symbol, headlines=headlines_block(headlines))
    content = await llm.llm_chat(prompt, temperature=0.1, max_tokens=200, timeout=25.0)
    if not content:
        base["summary"] = f"{len(headlines)} headlines; LLM unavailable."
        return base

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        base["summary"] = f"{len(headlines)} headlines; unparseable LLM reply."
        return base
    try:
        d = json.loads(m.group())
    except Exception:
        base["summary"] = f"{len(headlines)} headlines; bad LLM JSON."
        return base

    action = str(d.get("action", "HOLD")).upper()
    if action not in ("BUY", "SELL", "HOLD"):
        action = "HOLD"
    sentiment = str(d.get("sentiment", "neutral")).lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"
    try:
        score = max(-1.0, min(1.0, float(d.get("score", 0.0))))
    except Exception:
        score = 0.0
    try:
        confidence = max(0.0, min(0.95, float(d.get("confidence", 0.4))))
    except Exception:
        confidence = 0.4

    base.update({
        "sentiment": sentiment, "score": round(score, 3), "action": action,
        "confidence": round(confidence, 3),
        "catalyst": str(d.get("catalyst", "none"))[:80],
        "summary": str(d.get("summary", ""))[:140],
        "top_headlines": [h["title"] for h in headlines[:3]],
    })
    return base


async def refresh_all() -> dict:
    _state["running"] = True
    items = await _watchlist()
    analyzed = with_news = 0
    r = await _get_redis()
    for it in items:
        sym = (it.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            res = await analyze_symbol(sym, it.get("name", sym))
            await r.set(_sentiment_key(sym), json.dumps(res), ex=SENTIMENT_TTL)
            analyzed += 1
            if res.get("headlines_count", 0) > 0:
                with_news += 1
        except Exception as exc:
            logger.debug("analyze %s failed: %s", sym, exc)
        await asyncio.sleep(FETCH_DELAY)

    _state.update({"last_run": datetime.now(IST).isoformat(), "analyzed": analyzed,
                   "with_news": with_news, "running": False,
                   "provider": llm.resolve_provider(), "model": llm.active_model()})
    logger.info("sentiment refresh: %d analyzed, %d with news (%s/%s)",
                analyzed, with_news, _state["provider"], _state["model"])
    return {"analyzed": analyzed, "with_news": with_news}


async def get_sentiment(symbol: str) -> dict | None:
    try:
        r = await _get_redis()
        raw = await r.get(_sentiment_key(symbol))
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def sentiment_loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await refresh_all()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("sentiment loop error: %s", exc)
        await asyncio.sleep(SENTIMENT_INTERVAL)


def get_state() -> dict:
    return dict(_state)
