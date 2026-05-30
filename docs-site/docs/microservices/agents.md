---
id: agents
title: Agent Services
sidebar_position: 2
---

# Agent Services — Ports 8002–8006

All five agents share the same pattern: consume a market data tick, compute a signal, publish to `agent.signals`.

```
market.data (fanout)
  ├─ market.data.technical  → technical-agent  :8002
  ├─ market.data.sentiment  → sentiment-agent  :8003
  ├─ market.data.macro      → macro-agent      :8004
  ├─ market.data.pattern    → pattern-agent    :8005
  └─ market.data.rl         → rl-agent         :8006
                                    │
                                    ▼
                          agent.signals (direct)
                          routing_key = agent_name
```

## Signal Message Shape

All agents publish the same schema to `agent.signals`:

```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "agent": "technical",
  "signal": "BUY",
  "confidence": 0.78,
  "reasoning": "RSI oversold, MACD crossover",
  "indicators": { "rsi": 28.4, "macd": 0.12 },
  "model_votes": { "buy": 3, "sell": 1, "hold": 1 }
}
```

---

## Technical Agent — Port 8002

**Consumer:** [`technical-agent/app/consumer.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/technical-agent/app/consumer.py) · `start_consuming()` at [line 66](https://github.com/AbhinavShah421/NeuradeX/blob/main/technical-agent/app/consumer.py#L66)

| | |
|---|---|
| **Consumes** | Queue: `market.data.technical` |
| **Publishes** | Exchange: `agent.signals` · routing_key: `technical` |
| **Data** | PostgreSQL `ohlcv` — last `CANDLE_HISTORY_LIMIT` (200) candles |
| **Signals** | RSI, MACD, Bollinger Bands, SMA/EMA crossovers |

---

## Sentiment Agent — Port 8003

| | |
|---|---|
| **Consumes** | Queue: `market.data.sentiment` |
| **Publishes** | Exchange: `agent.signals` · routing_key: `sentiment` |
| **Model** | FinBERT (`ProsusAI/finbert`) |
| **Data** | MongoDB `news` — last `SENTIMENT_WINDOW_MINUTES` (60 min) articles |

---

## Macro Agent — Port 8004

| | |
|---|---|
| **Consumes** | Queue: `market.data.macro` |
| **Publishes** | Exchange: `agent.signals` · routing_key: `macro` |
| **Data** | External macro APIs; cached in Redis with TTL `MACRO_REFRESH_SECONDS` (3600s) |

---

## Pattern Agent — Port 8005

| | |
|---|---|
| **Consumes** | Queue: `market.data.pattern` |
| **Publishes** | Exchange: `agent.signals` · routing_key: `pattern` |
| **Data** | PostgreSQL `ohlcv` — last `CANDLE_HISTORY_LIMIT` (100) candles |
| **Signals** | Doji, Engulfing, Hammer, Morning/Evening Star, etc. |

---

## RL Agent — Port 8006

| | |
|---|---|
| **Consumes** | Queue: `market.data.rl` AND `trade.outcomes.rl` |
| **Publishes** | Exchange: `agent.signals` · routing_key: `rl` |
| **Model** | Reinforcement learning policy loaded from MLflow (`POLICY_MODEL_NAME="rl-trading-policy"`) |
| **Learning** | Consumes `trade.outcomes.rl` to update policy from real trade feedback |
