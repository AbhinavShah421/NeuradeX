"""Sentiment Agent — the ensemble's one price-independent voice.

It reads the LLM + FinBERT news signal produced off the hot path by the
sentiment-service and only votes directionally (BUY/SELL) when there is a
genuinely strong, *catalyst-backed* news signal. Otherwise it ABSTAINS (HOLD).

Training mechanisms built in:
  1. **FinBERT + LLM fusion** — the pipeline blends both scores before caching;
     provider field carries "+finbert_agree" / "+finbert_disagree" for audit.
  2. **Gate calibration from outcomes** — rolling BUY/SELL accuracy stored in
     Redis (ai_engine:sentiment_perf:{BUY|SELL}) by LearningSystem.record_outcome().
     Gates tighten when accuracy < 45%, relax slightly above 65%.
  3. **Catalyst-type weighting** — catalyst text is classified into categories
     (earnings, rating, M&A, legal, etc.) and each category has a confidence
     multiplier reflecting its historical reliability.
  4. **Momentum-sentiment alignment** — confidence is discounted when the news
     direction opposes price momentum (overbought + bullish news, oversold + bearish).
"""
from __future__ import annotations
import os
from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# ── Hard-coded gate defaults (env-overridable) ────────────────────────────────
NEWS_MIN_CONFIDENCE = float(os.getenv("NEWS_MIN_CONFIDENCE", "0.7"))
NEWS_MIN_SCORE      = float(os.getenv("NEWS_MIN_SCORE", "0.4"))

# Dynamic gate bounds — never tighten past ceil, never relax past floor
_GATE_FLOOR_CONF  = 0.55
_GATE_CEIL_CONF   = 0.85
_GATE_FLOOR_SCORE = 0.30
_GATE_CEIL_SCORE  = 0.55

# Minimum trades before dynamic gates kick in — below this, use defaults
_GATE_MIN_TRADES = 20

# ── Catalyst-type weights ─────────────────────────────────────────────────────
# Each category has a confidence multiplier (> 1 boosts, < 1 discounts).
# High-multiplier categories are specific, unambiguous, historically predictive.
# Low-multiplier categories are soft / forward-looking / noisy.
_CATALYST_MULTIPLIER: dict[str, float] = {
    "earnings":  1.25,   # quarterly results, profit beat/miss — most predictive
    "rating":    1.15,   # upgrade/downgrade — often precedes price move
    "m_and_a":   1.20,   # merger/acquisition — strong catalyst
    "buyback":   1.15,   # share repurchase — management conviction
    "contract":  1.10,   # contract win, order — tangible revenue
    "legal":     1.10,   # regulatory action, fraud, penalty — real impact
    "target":    0.80,   # price target raised/cut — soft, often backward-looking
    "outlook":   0.85,   # guidance, forecast — subjective
    "sector":    0.75,   # sector commentary — not stock-specific
}

# ── Catalyst keyword classifier ───────────────────────────────────────────────
_CATALYST_RULES: list[tuple[str, list[str]]] = [
    ("earnings",  ["q1 ", "q2 ", "q3 ", "q4 ", "quarterly", "annual result", "profit",
                   "revenue", "eps", "net income", "beat estimate", "miss estimate",
                   "earnings", "results"]),
    ("rating",    ["upgrade", "downgrade", "buy rating", "sell rating", "outperform",
                   "underperform", "overweight", "underweight", "analyst rating"]),
    ("m_and_a",   ["merger", "acquisition", "acquire", "takeover", "buyout",
                   "merge", "stake purchase"]),
    ("buyback",   ["buyback", "share repurchase", "buy back"]),
    ("contract",  ["contract", "order win", "wins deal", "awarded", "secures order",
                   "new order"]),
    ("legal",     ["fraud", "sebi", "regulatory", "penalty", "court", "cbi",
                   "enforcement directorate", "ed ", "rbi action", "fine"]),
    ("target",    ["price target", "target price", "fair value", "tp raised",
                   "tp cut"]),
    ("outlook",   ["outlook", "forecast", "expects", "projects", "guidance",
                   "management says"]),
    ("sector",    ["sector", "industry", "entire market", "peers", "peer group"]),
]

_VAGUE_CATALYSTS = {"", "none", "n/a", "na", "no catalyst", "no specific catalyst",
                    "no clear catalyst", "mixed", "unclear", "-"}


def _classify_catalyst(catalyst: str, summary: str) -> str:
    text = (catalyst + " " + summary).lower()
    for category, keywords in _CATALYST_RULES:
        if any(k in text for k in keywords):
            return category
    return "unknown"


# ── Dynamic gate calibration ──────────────────────────────────────────────────

async def _dynamic_gates() -> tuple[float, float]:
    """Read rolling sentiment accuracy from Redis and return adjusted (min_conf, min_score).

    BUY/SELL accuracy < 45%: gates tighten (require stronger signals to act).
    BUY/SELL accuracy > 65%: gates relax slightly (agent is demonstrably reliable).
    Otherwise: use env defaults.
    """
    try:
        from app.utils.redis_client import get_redis
        r = get_redis()
        buy_h  = await r.hgetall("ai_engine:sentiment_perf:BUY")
        sell_h = await r.hgetall("ai_engine:sentiment_perf:SELL")

        def _int(h: dict, k: str) -> int:
            v = h.get(k) or h.get(k.encode(), b"0")
            return int(v) if v else 0

        total   = _int(buy_h, "total")   + _int(sell_h, "total")
        correct = _int(buy_h, "correct") + _int(sell_h, "correct")

        if total < _GATE_MIN_TRADES:
            return NEWS_MIN_CONFIDENCE, NEWS_MIN_SCORE

        accuracy = correct / total
        if accuracy < 0.45:
            # Worse than random: tighten proportionally
            factor = min(1.4, 0.45 / max(accuracy, 0.10))
            conf  = min(_GATE_CEIL_CONF,  NEWS_MIN_CONFIDENCE * factor)
            score = min(_GATE_CEIL_SCORE, NEWS_MIN_SCORE      * factor)
        elif accuracy > 0.65:
            # Reliably good: relax slightly (capped at floor)
            factor = min(1.25, accuracy / 0.65)
            conf  = max(_GATE_FLOOR_CONF,  NEWS_MIN_CONFIDENCE / factor)
            score = max(_GATE_FLOOR_SCORE, NEWS_MIN_SCORE      / factor)
        else:
            conf, score = NEWS_MIN_CONFIDENCE, NEWS_MIN_SCORE

        return round(conf, 2), round(score, 2)
    except Exception:
        return NEWS_MIN_CONFIDENCE, NEWS_MIN_SCORE


# ── RSI helper (mirrors TechnicalAgent._rsi) ──────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(0.0, d) for d in deltas]
    losses = [-min(0.0, d) for d in deltas]
    ag = sum(gains[:period])  / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i])  / period
        al = (al * (period - 1) + losses[i]) / period
    return 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))


class SentimentAgent(BaseAgent):
    name = "sentiment"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 5:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data")

        date = None
        if context.get("mode") in ("backtest", "replay"):
            date = context.get("date")

        signal = await self._news_signal(symbol, date=date)

        # Momentum-sentiment alignment: discount confidence when news direction
        # opposes current price momentum (overbought + bullish, oversold + bearish).
        if signal.action in ("BUY", "SELL") and len(candles) >= 15:
            signal = self._momentum_alignment(signal, candles)

        return signal

    def _momentum_alignment(self, signal: AgentSignal, candles: list[dict]) -> AgentSignal:
        closes = [c["close"] for c in candles]
        rsi    = _rsi(closes)
        conf   = signal.confidence
        note   = ""

        if signal.action == "BUY" and rsi > 72:
            discount = 0.25 if rsi > 80 else 0.15
            conf = round(conf * (1 - discount), 3)
            note = f"; overbought RSI {rsi:.0f} — confidence trimmed"
        elif signal.action == "SELL" and rsi < 28:
            discount = 0.25 if rsi < 20 else 0.15
            conf = round(conf * (1 - discount), 3)
            note = f"; oversold RSI {rsi:.0f} — confidence trimmed"

        # Secondary: SMA5 vs SMA20 trend direction
        if len(closes) >= 20:
            sma5  = sum(closes[-5:])  / 5
            sma20 = sum(closes[-20:]) / 20
            if signal.action == "BUY" and sma5 < sma20 and not note:
                conf = round(conf * 0.90, 3)
                note = "; downtrend (SMA5<SMA20) tempers bullish news"
            elif signal.action == "SELL" and sma5 > sma20 and not note:
                conf = round(conf * 0.90, 3)
                note = "; uptrend (SMA5>SMA20) tempers bearish news"

        if not note:
            return signal

        ind = dict(signal.indicators or {})
        ind["momentum_note"] = note.lstrip("; ")
        return AgentSignal(
            agent_name=self.name,
            action=signal.action,
            confidence=conf,
            reasoning=signal.reasoning + note,
            indicators=ind,
        )

    async def _news_signal(self, symbol: str, date: str | None = None) -> AgentSignal:
        import json
        abstain = AgentSignal(
            agent_name=self.name, action="HOLD", confidence=0.4,
            reasoning="No strong news catalyst — abstaining",
            indicators={"source": "news_llm", "status": "abstain"},
        )
        try:
            from app.utils.redis_client import cache_get
            sym = symbol.upper()
            if date:
                redis_key = f"ai_engine:sentiment:{sym}:{date}"
                raw = await cache_get(redis_key)
                if not raw:
                    try:
                        import asyncio
                        from app.agents.sentiment_pipeline import run_pipeline_for_date
                        asyncio.create_task(run_pipeline_for_date(sym, date))
                    except Exception:
                        pass
                    return abstain
            else:
                raw = await cache_get(f"ai_engine:sentiment:{sym}")
                if not raw:
                    try:
                        import asyncio
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
        catalyst = str(d.get("catalyst", "") or "").strip()
        summary  = str(d.get("summary",  "") or "").strip()
        has_catalyst = catalyst.lower() not in _VAGUE_CATALYSTS

        if not (sentiment in ("positive", "negative") and has_catalyst):
            return abstain

        # Dynamic gates — calibrated from rolling trade outcomes
        min_conf, min_score = await _dynamic_gates()

        if conf < min_conf or score < min_score:
            return abstain

        # Catalyst-type confidence multiplier
        cat   = _classify_catalyst(catalyst, summary)
        mult  = _CATALYST_MULTIPLIER.get(cat, 1.0)
        conf  = round(min(0.95, conf * mult), 3)

        action = "BUY" if sentiment == "positive" else "SELL"
        finbert_note = ""
        finbert_net  = d.get("finbert_net")
        if finbert_net is not None:
            finbert_note = f" [FinBERT net={finbert_net:+.2f}]"

        return AgentSignal(
            agent_name=self.name, action=action, confidence=conf,
            reasoning=(
                f"{str(d.get('summary') or catalyst)[:120]}"
                f" [{cat}×{mult:.2f}]{finbert_note}"
            ),
            indicators={
                "source":       "news_llm",
                "sentiment":    sentiment,
                "score":        d.get("score"),
                "catalyst":     catalyst,
                "catalyst_cat": cat,
                "catalyst_mult": mult,
                "headlines":    d.get("headlines_count"),
                "provider":     d.get("provider"),
                "finbert_net":  finbert_net,
                "date":         d.get("date"),
                "min_conf_used": min_conf,
                "min_score_used": min_score,
            },
        )
