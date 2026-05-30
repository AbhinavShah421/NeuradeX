---
id: stock-detail
title: Stock Detail
sidebar_label: Stock Detail
---

# Stock Detail (`/stocks/:symbol`)

**File:** `frontend/src/pages/StockDetail.tsx`

Deep-dive page for a single stock: live quote, AI prediction, news sentiment, prediction history, and order placement.

## API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/stocks/{symbol}` | On mount / symbol change | Live quote, OHLCV, fundamentals |
| 2 | GET | `/api/predictions/{symbol}` | On mount / symbol change | Current AI prediction (signal, confidence, targets) |
| 3 | GET | `/api/stocks/{symbol}/sentiment` | On mount / symbol change | News & social sentiment score |
| 4 | GET | `/api/predictions/{symbol}/history` | On mount / symbol change | Last 20 historical predictions |
| 5 | POST | `/api/orders/` | On "Place Order" submit | Place a live/paper trade order |

## Data Flow

```
URL: /stocks/RELIANCE
    │
    ├─ GET /api/stocks/RELIANCE
    │      → { symbol, price, change, changePercent, volume,
    │           high52w, low52w, marketCap, peRatio, ... }
    │
    ├─ GET /api/predictions/RELIANCE
    │      → { signal: "BUY", confidence: 0.82,
    │           targetPrice: 2850, stopLoss: 2650,
    │           riskRewardRatio: 1.8, reasoning: "..." }
    │
    ├─ GET /api/stocks/RELIANCE/sentiment
    │      → { score: 0.65, label: "POSITIVE",
    │           newsItems: [{ headline, source, publishedAt }] }
    │
    └─ GET /api/predictions/RELIANCE/history?limit=20
           → [{ signal, confidence, targetPrice, createdAt, outcome }]

[Place Order]
    │
    └─ POST /api/orders/
           body: {
             symbol: "RELIANCE",
             side: "BUY",            // or "SELL"
             order_type: "MARKET",   // or "LIMIT"
             quantity: 10,
             price: 2800.00,         // only for LIMIT
             product: "CNC"          // or "MIS" (intraday)
           }
           → { order_id, status, message }
```

## Real-time Updates

The page subscribes to the Socket.IO room for the stock symbol:

```
socket.emit('join', { symbol: 'RELIANCE' })
socket.on('tick', (data) => update stock price + change)
socket.on('prediction', (data) => update prediction badge)
```

## Order Form

| Field | Options |
|---|---|
| Side | BUY / SELL |
| Order Type | MARKET / LIMIT |
| Quantity | Number input |
| Price | Only shown for LIMIT orders |
| Product | CNC (delivery) / MIS (intraday) |

## State

```typescript
stock: Stock | null
prediction: Prediction | null
sentiment: SentimentData | null
predictionHistory: Prediction[]
// Order form
orderSide: 'BUY' | 'SELL'
orderType: 'MARKET' | 'LIMIT'
orderQty: number
orderPrice: number
orderProduct: 'CNC' | 'MIS'
```
