"""FinBERT sentiment scorer — lazily loaded on first use."""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Exponential decay half-life for news articles.
# At 12 hours an article retains 50% weight; at 24h → 25%; at 48h → 6%.
_DECAY_HALF_LIFE_HOURS = 12.0
_DECAY_LAMBDA = math.log(2) / _DECAY_HALF_LIFE_HOURS


def _recency_weight(published_at: str | None, now: datetime | None = None) -> float:
    """Return exp(-λ·age_hours) in (0, 1]. Returns 1.0 if published_at missing."""
    if not published_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(tz=timezone.utc)
        age_hours = max(0.0, (ref - ts).total_seconds() / 3600.0)
        return math.exp(-_DECAY_LAMBDA * age_hours)
    except Exception:
        return 1.0

_pipeline = None


def _load_pipeline(model_name: str = "ProsusAI/finbert") -> None:
    global _pipeline
    if _pipeline is not None:
        return
    try:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline(
            "text-classification",
            model=model_name,
            tokenizer=model_name,
            top_k=None,
            max_length=512,
            truncation=True,
        )
        logger.info("FinBERT loaded: %s", model_name)
    except Exception as exc:
        logger.error("FinBERT load failed: %s — using keyword fallback", exc)


def score_text(text: str, model_name: str = "ProsusAI/finbert") -> dict:
    """
    Returns { "positive": float, "negative": float, "neutral": float }.
    All values in [0, 1] and sum to 1.
    Falls back to keyword heuristics if model unavailable.
    """
    _load_pipeline(model_name)
    if _pipeline is not None:
        try:
            results = _pipeline(text[:512])
            scores = {r["label"].lower(): r["score"] for r in results[0]}
            return {
                "positive": scores.get("positive", 0.0),
                "negative": scores.get("negative", 0.0),
                "neutral": scores.get("neutral", 1.0),
            }
        except Exception as exc:
            logger.warning("FinBERT inference error: %s", exc)

    return _keyword_fallback(text)


def _keyword_fallback(text: str) -> dict:
    text_lower = text.lower()
    bullish_words = ["surge", "rally", "gain", "profit", "growth", "beat", "upgrade", "buy", "strong", "record", "rise", "jump"]
    bearish_words = ["fall", "drop", "loss", "crash", "cut", "downgrade", "sell", "weak", "miss", "decline", "slump", "concern"]
    bull_count = sum(1 for w in bullish_words if w in text_lower)
    bear_count = sum(1 for w in bearish_words if w in text_lower)
    total = bull_count + bear_count + 1
    pos = bull_count / total
    neg = bear_count / total
    neu = 1.0 - pos - neg
    return {"positive": round(pos, 3), "negative": round(neg, 3), "neutral": round(neu, 3)}


def aggregate_scores(
    articles: list[dict],
    source_weights: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """
    Aggregate per-article scores into a symbol-level sentiment signal.
    articles: list of { raw_text, source, published_at, scores: {...} }
    Each article's effective weight = source_weight × recency_decay.
    Returns: { net_sentiment, bullish_score, bearish_score, article_count, effective_articles }
    """
    default_weights = {
        "reuters": 1.0, "bloomberg": 1.0,
        "economic times": 0.8, "moneycontrol": 0.8, "livemint": 0.8,
        "twitter": 0.5, "reddit": 0.4,
    }
    src_w = source_weights or default_weights

    if not articles:
        return {
            "net_sentiment": 0.0, "bullish_score": 0.0,
            "bearish_score": 0.0, "article_count": 0, "effective_articles": 0.0,
        }

    weighted_pos = 0.0
    weighted_neg = 0.0
    total_weight = 0.0

    for a in articles:
        src = (a.get("source", "") or "").lower()
        source_multiplier = 0.6
        for key, val in src_w.items():
            if key in src:
                source_multiplier = val
                break

        decay = _recency_weight(a.get("published_at"), now)
        w = source_multiplier * decay

        sc = a.get("scores", {})
        weighted_pos += sc.get("positive", 0.0) * w
        weighted_neg += sc.get("negative", 0.0) * w
        total_weight += w

    if total_weight == 0:
        return {
            "net_sentiment": 0.0, "bullish_score": 0.0,
            "bearish_score": 0.0, "article_count": len(articles), "effective_articles": 0.0,
        }

    bull = weighted_pos / total_weight
    bear = weighted_neg / total_weight
    net = round(bull - bear, 4)

    return {
        "net_sentiment": net,
        "bullish_score": round(bull, 4),
        "bearish_score": round(bear, 4),
        "article_count": len(articles),
        "effective_articles": round(total_weight, 2),
    }
