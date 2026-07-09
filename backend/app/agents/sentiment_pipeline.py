"""Sentiment pipeline — news fetch + LLM analysis → Redis.

Feeds the SentimentAgent's Redis key so the agent has a real signal to vote on.

Flow:
  1. fetch_headlines()   — Google News RSS (primary) → Yahoo Finance RSS (fallback)
  2. analyze_with_llm()  — structured prompt → JSON from active LLM provider
  3. run_pipeline()      — checks cache TTL, runs 1+2, writes result to Redis

Callers:
  • POST /api/ai-engine/sentiment/refresh?symbol=SBIN  (on-demand UI button)
  • SentimentAgent._news_signal() fires asyncio.create_task(run_pipeline())
    when Redis returns nothing — result arrives in the next ensemble call
  • Never called in replay mode (ensemble skips sentiment in replay anyway)

Output schema (written to Redis ai_engine:sentiment:{SYMBOL}):
  {
    "sentiment":       "positive" | "negative" | "neutral",
    "score":           float  -1.0 to 1.0,
    "confidence":      float   0.0 to 1.0,
    "catalyst":        "specific event or empty string",
    "summary":         "one-sentence finding",
    "headlines_count": int,
    "headlines":       [str, ...],   // top 3 shown in UI
    "provider":        "google_news+llm" | "yahoo_finance+llm" | ...,
    "fetched_at":      unix-timestamp float,
  }
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import aiohttp

from app.utils.elk_logger import get_logger
from app.utils.llm_client import llm_chat

logger = get_logger(__name__)

_CACHE_TTL          = 900    # 15 min: don't re-fetch unless stale
_MIN_REFRESH        = 120    # hard minimum between fetches per symbol (avoid hammering)
_MAX_HEADLINES      = 6
_HIST_CACHE_TTL     = 86400  # historical results are immutable — cache 24 h

# FinBERT service URL — blends transformer scores with LLM catalyst analysis.
_FINBERT_URL = os.getenv("SENTIMENT_AGENT_URL", "http://sentiment-agent:8003")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a financial news analyst specializing in Indian equity markets (NSE/BSE). "
    "Analyze headlines and return ONLY valid JSON — no preamble, no explanation."
)

_USER_TMPL = """Analyze these news headlines for {symbol} stock (NSE/BSE):

{headlines}

Return ONLY this JSON, nothing else:
{{
  "sentiment": "positive" | "negative" | "neutral",
  "score": <float -1.0 to 1.0>,
  "confidence": <float 0.0 to 1.0>,
  "catalyst": "<specific event e.g. 'Q4 profit up 18%', or empty string>",
  "summary": "<one-sentence key finding>"
}}

Rules:
- positive (score > 0.3): earnings beat, rating upgrade, contract win, buyback, FII buying
- negative (score < -0.3): earnings miss, downgrade, fraud, regulatory action, promoter selling
- neutral (score ≈ 0): mixed news, price targets, sector commentary, no company-specific event
- confidence > 0.7 ONLY for specific, recent, unambiguous company-level events
- catalyst: the exact event in plain English; empty string "" if no specific catalyst found
"""


# ── FinBERT fusion ────────────────────────────────────────────────────────────

async def _finbert_blend(headlines: list[str]) -> float | None:
    """Call the FinBERT microservice to get a net_sentiment score for headlines.

    Returns net_sentiment in [-1, 1] or None if the service is unreachable.
    The caller uses this to boost/discount LLM confidence based on whether
    both models agree on direction — cross-model agreement is a strong signal.
    """
    if not headlines:
        return None
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{_FINBERT_URL}/score",
                json={"headlines": headlines},
                timeout=aiohttp.ClientTimeout(total=4),
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    net = d.get("data", {}).get("net_sentiment")
                    return float(net) if net is not None else None
    except Exception as exc:
        logger.debug("finbert_blend unavailable: %s", exc)
    return None


def _apply_finbert(result: dict, finbert_net: float | None) -> dict:
    """Adjust LLM result confidence based on FinBERT agreement/disagreement.

    Agreement (+same direction, |net| > 0.1): confidence × 1.15 — two independent
    models agreeing is a stronger signal than either alone.
    Disagreement (opposing direction, |net| > 0.15): confidence × 0.80 — models
    disagree on the fundamental direction, so trust the composite signal less.
    """
    if finbert_net is None:
        return result
    result = dict(result)
    llm_pos = result.get("sentiment") == "positive"
    llm_neg = result.get("sentiment") == "negative"
    finbert_pos = finbert_net > 0.10
    finbert_neg = finbert_net < -0.10
    result["finbert_net"] = round(finbert_net, 3)
    if (llm_pos and finbert_pos) or (llm_neg and finbert_neg):
        result["confidence"] = round(min(0.95, result.get("confidence", 0) * 1.15), 3)
        result["provider"] = result.get("provider", "") + "+finbert_agree"
    elif (llm_pos and finbert_neg and finbert_net < -0.15) or \
         (llm_neg and finbert_pos and finbert_net > 0.15):
        result["confidence"] = round(result.get("confidence", 0) * 0.80, 3)
        result["provider"] = result.get("provider", "") + "+finbert_disagree"
    return result


# ── News fetchers ─────────────────────────────────────────────────────────────

async def _fetch_google_news(symbol: str, n: int) -> tuple[list[str], str]:
    """Returns (headlines, provider_label)."""
    query = f"{symbol}+NSE+stock+India"
    url   = (f"https://news.google.com/rss/search?q={query}"
             f"&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return [], "google_news_error"
                text = await r.text()
        root  = ET.fromstring(text)
        items = root.findall(".//item")[:n]
        out   = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            # Google News appends " - Source Name" — strip it
            title = re.sub(r"\s+[-–]\s+\S.*$", "", title).strip()
            if title:
                out.append(title)
        return out, "google_news"
    except Exception as exc:
        logger.debug("google_news fetch failed for %s: %s", symbol, exc)
        return [], "google_news_error"


async def _fetch_yahoo_rss(symbol: str, n: int) -> tuple[list[str], str]:
    """Yahoo Finance RSS for NSE stocks ({SYMBOL}.NS)."""
    url = (f"https://feeds.finance.yahoo.com/rss/2.0/headline"
           f"?s={symbol}.NS&region=IN&lang=en-US")
    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return [], "yahoo_error"
                text = await r.text()
        root  = ET.fromstring(text)
        items = root.findall(".//item")[:n]
        out   = [(item.findtext("title") or "").strip() for item in items]
        return [t for t in out if t], "yahoo_finance"
    except Exception as exc:
        logger.debug("yahoo_rss fetch failed for %s: %s", symbol, exc)
        return [], "yahoo_error"


async def fetch_headlines(symbol: str, max_items: int = _MAX_HEADLINES) -> tuple[list[str], str]:
    """Fetch financial headlines. Google News primary, Yahoo fallback.
    Returns (headlines, provider_label).
    """
    headlines, provider = await _fetch_google_news(symbol, max_items)
    if len(headlines) >= 2:
        return headlines[:max_items], provider

    # Fallback to Yahoo Finance
    headlines2, prov2 = await _fetch_yahoo_rss(symbol, max_items)
    if headlines2:
        combined = headlines + [h for h in headlines2 if h not in headlines]
        return combined[:max_items], f"{provider}+{prov2}"

    return headlines, provider


async def fetch_headlines_for_date(symbol: str, date: str,
                                   max_items: int = _MAX_HEADLINES) -> tuple[list[str], str]:
    """Fetch headlines published on or around `date` (YYYY-MM-DD).

    Uses Google News RSS date-range operators so results reflect what the market
    knew on that specific trading day, not today's news.  The window is
    [date-1, date+1] so we capture pre-market news as well as same-day items.
    Falls back to the live feed (no date filter) if the dated query returns nothing —
    this handles very recent dates where date-range indexing isn't complete yet.
    """
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        after  = (d - timedelta(days=1)).strftime("%Y-%m-%d")
        before = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        return await fetch_headlines(symbol, max_items)

    # Google News date-range query
    query = f"{symbol}+NSE+stock+India+after:{after}+before:{before}"
    url   = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    text = await r.text()
                    root  = ET.fromstring(text)
                    items = root.findall(".//item")[:max_items]
                    out   = []
                    for item in items:
                        title = (item.findtext("title") or "").strip()
                        title = re.sub(r"\s+[-–]\s+\S.*$", "", title).strip()
                        if title:
                            out.append(title)
                    if len(out) >= 2:
                        return out, "google_news_dated"
    except Exception as exc:
        logger.debug("google_news_dated fetch failed for %s on %s: %s", symbol, date, exc)

    # Fallback: undated live feed (still better than nothing)
    return await fetch_headlines(symbol, max_items)


async def run_pipeline_for_date(symbol: str, date: str, force: bool = False) -> dict:
    """Fetch news for a specific past date, run LLM, cache in a date-keyed Redis key.

    Key: ai_engine:sentiment:{SYMBOL}:{DATE}  (24-h TTL — historical data is immutable)

    This is the entry point for backtest/replay historical sentiment pre-fetching.
    The SentimentAgent reads this key (via context["date"]) during ensemble calls
    when the session mode is replay or backtest.
    """
    sym = symbol.upper()
    redis_key = f"ai_engine:sentiment:{sym}:{date}"

    try:
        from app.utils.redis_client import cache_get, cache_set
    except Exception:
        logger.error("redis_client import failed; historical sentiment unavailable", exc_info=True)
        return {"status": "error", "detail": "redis unavailable"}

    if not force:
        try:
            raw = await cache_get(redis_key)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.debug("historical sentiment cache_get failed for %s/%s", sym, date, exc_info=True)

    headlines, provider = await fetch_headlines_for_date(sym, date)

    if not headlines:
        result: dict = {
            "sentiment": "neutral", "score": 0.0, "confidence": 0.0,
            "catalyst": "", "summary": "No headlines found for this date",
            "headlines_count": 0, "headlines": [], "top_headlines": [],
            "provider": provider, "date": date, "fetched_at": time.time(),
        }
        try:
            await cache_set(redis_key, json.dumps(result), expire=_HIST_CACHE_TTL)
        except Exception:
            logger.debug("historical sentiment cache_set (empty result) failed for %s/%s", sym, date, exc_info=True)
        return result

    # Run LLM and FinBERT in parallel — both score the same headlines independently.
    analysis, finbert_net = await asyncio.gather(
        analyze_with_llm(sym, headlines),
        _finbert_blend(headlines),
        return_exceptions=True,
    )
    if isinstance(analysis, Exception):
        analysis = None
    if isinstance(finbert_net, Exception):
        finbert_net = None

    top3 = headlines[:3]
    if analysis:
        result = {
            "sentiment":       str(analysis.get("sentiment", "neutral")).lower(),
            "score":           float(analysis.get("score",      0) or 0),
            "confidence":      float(analysis.get("confidence", 0) or 0),
            "catalyst":        str(analysis.get("catalyst",    "") or "").strip(),
            "summary":         str(analysis.get("summary",     "") or "").strip(),
            "headlines_count": len(headlines),
            "headlines":       top3,
            "top_headlines":   top3,
            "provider":        f"{provider}+llm",
            "date":            date,
            "fetched_at":      time.time(),
        }
        result = _apply_finbert(result, finbert_net)
    else:
        result = {
            "sentiment": "neutral", "score": 0.0, "confidence": 0.0,
            "catalyst": "", "summary": "LLM analysis unavailable",
            "headlines_count": len(headlines), "headlines": top3, "top_headlines": top3,
            "provider": provider, "date": date, "fetched_at": time.time(),
        }

    try:
        await cache_set(redis_key, json.dumps(result), expire=_HIST_CACHE_TTL)
    except Exception as exc:
        logger.warning("historical sentiment cache_set failed for %s/%s: %s", sym, date, exc)

    logger.info(
        "Historical sentiment: %s @ %s → %s (score %.2f, conf %.2f, %d headlines)",
        sym, date, result["sentiment"], result["score"], result["confidence"],
        len(headlines),
        extra={"log_type": "ai_engine", "event": "historical_sentiment_pipeline"},
    )
    return result


# ── LLM analysis ─────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> Optional[dict]:
    """Parse LLM response: try direct JSON, then extract first {...} block."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*?\}", raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


async def analyze_with_llm(symbol: str, headlines: list[str]) -> Optional[dict]:
    """Send headlines to LLM; return parsed sentiment dict or None."""
    if not headlines:
        return None
    hl_text = "\n".join(f"- {h}" for h in headlines)
    prompt  = _USER_TMPL.format(symbol=symbol, headlines=hl_text)
    raw = await llm_chat(
        prompt, system=_SYSTEM,
        # 120s: the 18s budget was tuned for llama3.2-3B; host-side llama3.1:8b
        # needs ~30s+ per scoring call. This runs in the background refresh and
        # is cached 15 min per symbol, so latency is invisible to decisions —
        # a short timeout here just silently degrades sentiment to neutral.
        temperature=0.0, max_tokens=300, timeout=120.0,
    )
    if not raw:
        logger.debug("LLM returned nothing for %s", symbol)
        return None
    result = _extract_json(raw)
    if not result:
        logger.debug("LLM non-JSON for %s: %.200s", symbol, raw)
    return result


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(symbol: str, force: bool = False) -> dict:
    """Fetch news → LLM → Redis.  Returns the stored sentiment dict.

    Skips the fetch if cached data is still fresh (< _CACHE_TTL seconds old),
    unless force=True.  A hard minimum (_MIN_REFRESH) prevents hammering the
    news APIs even with force=True.
    """
    sym = symbol.upper()
    redis_key = f"ai_engine:sentiment:{sym}"

    try:
        from app.utils.redis_client import cache_get, cache_set
    except Exception:
        logger.error("redis_client import failed; sentiment pipeline unavailable", exc_info=True)
        return {"status": "error", "detail": "redis unavailable"}

    # Check existing cache
    if not force:
        try:
            raw = await cache_get(redis_key)
            if raw:
                d = json.loads(raw)
                age = time.time() - float(d.get("fetched_at", 0))
                if age < _CACHE_TTL:
                    return d     # still fresh
                if age < _MIN_REFRESH:
                    return d     # don't hammer even if stale
        except Exception:
            logger.debug("sentiment cache_get failed for %s", sym, exc_info=True)

    # Fetch headlines
    headlines, provider = await fetch_headlines(sym)

    if not headlines:
        result: dict = {
            "sentiment":      "neutral",
            "score":          0.0,
            "confidence":     0.0,
            "catalyst":       "",
            "summary":        "No headlines found",
            "headlines_count": 0,
            "headlines":      [],
            "top_headlines":  [],
            "provider":       provider,
            "fetched_at":     time.time(),
        }
        try:
            await cache_set(redis_key, json.dumps(result), expire=_CACHE_TTL)
        except Exception:
            logger.debug("sentiment cache_set (empty result) failed for %s", sym, exc_info=True)
        return result

    # Run LLM and FinBERT in parallel — both score the same headlines independently.
    analysis, finbert_net = await asyncio.gather(
        analyze_with_llm(sym, headlines),
        _finbert_blend(headlines),
        return_exceptions=True,
    )
    if isinstance(analysis, Exception):
        analysis = None
    if isinstance(finbert_net, Exception):
        finbert_net = None

    top3 = headlines[:3]
    if analysis:
        result = {
            "sentiment":      str(analysis.get("sentiment", "neutral")).lower(),
            "score":          float(analysis.get("score",      0) or 0),
            "confidence":     float(analysis.get("confidence", 0) or 0),
            "catalyst":       str(analysis.get("catalyst",    "") or "").strip(),
            "summary":        str(analysis.get("summary",     "") or "").strip(),
            "headlines_count": len(headlines),
            "headlines":      top3,
            "top_headlines":  top3,
            "provider":       f"{provider}+llm",
            "fetched_at":     time.time(),
        }
        result = _apply_finbert(result, finbert_net)
    else:
        result = {
            "sentiment":      "neutral",
            "score":          0.0,
            "confidence":     0.0,
            "catalyst":       "",
            "summary":        "LLM analysis unavailable",
            "headlines_count": len(headlines),
            "headlines":      top3,
            "top_headlines":  top3,
            "provider":       provider,
            "fetched_at":     time.time(),
        }

    try:
        await cache_set(redis_key, json.dumps(result), expire=_CACHE_TTL)
    except Exception as exc:
        logger.warning("sentiment cache_set failed for %s: %s", sym, exc)

    logger.info(
        "Sentiment pipeline: %s → %s (score %.2f, conf %.2f, %d headlines)",
        sym, result["sentiment"], result["score"], result["confidence"],
        len(headlines),
        extra={"log_type": "ai_engine", "event": "sentiment_pipeline"},
    )
    return result
