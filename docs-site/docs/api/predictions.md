---
id: predictions
title: Predictions
sidebar_position: 3
---

# Predictions — `/api/predictions`

**File:** [`backend/app/api/predictions.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/predictions.py)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/predictions/{symbol}` | **Real** technical prediction for a stock (see below) |
| `POST` | `/api/predictions/{symbol}/custom-analysis` | On-demand deeper analysis |
| `GET` | `/api/predictions/{symbol}/history` | Historical predictions from PostgreSQL |
| `GET` | `/api/predictions/accuracy/stats` | **Real** win-rate / accuracy from `trade_records` + `ai_engine_outcomes` (win-rate, avg return, Sharpe, max drawdown, by-source) |

## How `GET /predictions/{symbol}` is computed

The prediction is computed from **live technicals — no random values**:

```
GET /predictions/{symbol}
  ├─ on the AI watchlist today?  → reuse the scanner's fresh analysis
  │                                 (source: "scanner", real Yahoo-derived metrics)
  └─ otherwise → fetch real daily candles (Groww → simulated fallback) and derive
                 direction from a weighted vote over RSI, 20/50-SMA trend,
                 10-bar momentum and ATR; target/stop sized from ATR
```

The response includes the computed `indicators` block (RSI, SMA20/50, momentum,
ATR%) and a `source` of `scanner` or a fresh technical compute.

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
