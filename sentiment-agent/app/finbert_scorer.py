"""FinBERT sentiment scorer — lazily loaded on first use."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

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


def aggregate_scores(articles: list[dict], source_weights: dict | None = None) -> dict:
    """
    Aggregate per-article scores into a symbol-level sentiment signal.
    articles: list of { raw_text, source, scores: {...} }
    Returns: { net_sentiment, bullish_score, bearish_score, article_count }
    """
    default_weights = {
        "reuters": 1.0, "bloomberg": 1.0,
        "economic times": 0.8, "moneycontrol": 0.8, "livemint": 0.8,
        "twitter": 0.5, "reddit": 0.4,
    }
    weights = source_weights or default_weights

    if not articles:
        return {"net_sentiment": 0.0, "bullish_score": 0.0, "bearish_score": 0.0, "article_count": 0}

    weighted_pos = 0.0
    weighted_neg = 0.0
    total_weight = 0.0

    for a in articles:
        src = (a.get("source", "") or "").lower()
        w = 0.6   # default weight for unknown source
        for key, val in weights.items():
            if key in src:
                w = val
                break
        sc = a.get("scores", {})
        weighted_pos += sc.get("positive", 0.0) * w
        weighted_neg += sc.get("negative", 0.0) * w
        total_weight += w

    if total_weight == 0:
        return {"net_sentiment": 0.0, "bullish_score": 0.0, "bearish_score": 0.0, "article_count": 0}

    bull = weighted_pos / total_weight
    bear = weighted_neg / total_weight
    net = round(bull - bear, 4)

    return {
        "net_sentiment": net,
        "bullish_score": round(bull, 4),
        "bearish_score": round(bear, 4),
        "article_count": len(articles),
    }
