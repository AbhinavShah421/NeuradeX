---
id: sentiment-service
title: Sentiment Service
sidebar_position: 7
---

# Sentiment Service — Port 8016

**Entry point:** [`sentiment-service/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/sentiment-service/app/main.py) ·
**Worker:** [`sentiment-service/app/sentiment.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/sentiment-service/app/sentiment.py)

Gives the ensemble a signal driven by **news**, genuinely independent of price.
Every 30 minutes it pulls recent headlines for each AI-watchlist stock, asks the
LLM to judge short-term sentiment, and caches the result in Redis for the
backend's `sentiment` agent to read.

```
AI watchlist ─▶ Google News RSS (per stock) ─▶ LLM judges sentiment ─▶ Redis ai_engine:sentiment:{SYMBOL}
                                                                              │
                                          backend `sentiment` agent reads it ─┘ (fast, on the hot path)
```

Why it matters: the other agents all read the same OHLCV candles, so their votes
are correlated. A news-derived signal is the one input that is *not* a function
of price — which is what gives the [ensemble](../microservices/ensemble-engine.md)
real diversity and lifts accuracy.

---

## News source — Google News RSS (free)

[`news.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/sentiment-service/app/news.py)
fetches the public Google-News RSS feed for each stock — **no API key required**:

```
https://news.google.com/rss/search?q="<name>" stock NSE&hl=en-IN&gl=IN&ceid=IN:en
```

It parses the XML with the stdlib (no extra dependency) and keeps the most
recent headlines.

## LLM judgement

For each stock the headlines are sent to the [LLM provider](../ai-engine/llm-provider.md)
(Anthropic or Ollama, auto-selected) with a strict JSON contract:

```json
{ "sentiment": "positive|negative|neutral", "score": 0.64,
  "action": "BUY|SELL|HOLD", "confidence": 0.83,
  "catalyst": "FIFA World Cup broadcast talks", "summary": "..." }
```

`score` is −1 (very bearish) … +1 (very bullish). If headlines are absent or
irrelevant the model returns neutral/HOLD with low confidence, and the backend
agent then falls back to its price rule rather than overriding it.

The result (plus `headlines_count`, `top_headlines`, `provider`, `updated_at`)
is written to `ai_engine:sentiment:{SYMBOL}` with a 90-minute TTL.

---

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness + worker state (last run, analyzed, with-news, provider, model) |
| `GET` | `/status` | Full state |
| `POST` | `/refresh` | Trigger an immediate news-sentiment sweep of the watchlist |
| `GET` | `/sentiment/{symbol}` | The cached signal for one stock |

## Redis keys

| Key | Writer | Reader | TTL | Purpose |
|---|---|---|---|---|
| `ai_engine:sentiment:{SYMBOL}` | sentiment-service | backend `sentiment` agent | 90 min | News-driven sentiment signal |

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `SENTIMENT_INTERVAL` | 1800 | Seconds between watchlist news sweeps (30 min) |
| `SENTIMENT_TTL` | 5400 | How long a cached signal stays valid (90 min) |
| `SENTIMENT_MAX_HEADLINES` | 8 | Headlines per stock sent to the LLM |
| `LLM_PROVIDER` / `ANTHROPIC_API_KEY` / `LLM_MODEL` | — | Same [LLM provider](../ai-engine/llm-provider.md) config as the backend |

> With **Ollama** the cost is zero; with **Anthropic** it's ~15 small calls per
> 30-min sweep. Claude reads financial news materially better than a small local
> model, so adding a key is the cheapest accuracy upgrade.
