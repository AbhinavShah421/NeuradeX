---
id: predictions
title: Predictions
sidebar_position: 3
---

# Predictions — `/api/predictions`

**File:** [`backend/app/api/predictions.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/predictions/{symbol}` | [34](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py#L34) | Latest ensemble prediction — reads Redis `ensemble:{symbol}` |
| `POST` | `/api/predictions/{symbol}/custom-analysis` | [107](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py#L107) | Trigger on-demand analysis |
| `GET` | `/api/predictions/{symbol}/history` | [156](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py#L156) | Historical predictions from PostgreSQL |
| `GET` | `/api/predictions/accuracy/stats` | [186](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py#L186) | Win-rate and accuracy metrics |

## Cache Behaviour

```
GET /predictions/{symbol}
  └─ Redis GET ensemble:{symbol}
       ├─ HIT  → return cached decision (set by ensemble-engine, TTL 300s)
       └─ MISS → return last known from PostgreSQL or trigger custom-analysis
```

## Response Example

```json
{
  "status": "success",
  "data": {
    "symbol": "RELIANCE",
    "prediction": "BUY",
    "confidence": 0.78,
    "target_price": 2950.0,
    "current_price": 2820.0,
    "stop_loss": 2760.0,
    "upside_potential": 4.6,
    "risk_reward_ratio": 2.2,
    "timeframe": "1-4 hours",
    "reasoning": "RSI oversold, MACD crossover confirmed by volume.",
    "factors": ["Technical Analysis", "Sentiment Analysis", "Pattern Recognition"],
    "timestamp": "2026-05-30T10:00:00Z"
  }
}
```
