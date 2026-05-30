---
id: portfolio-predictions
title: Portfolio & Predictions
sidebar_label: Portfolio & Predictions
---

# Portfolio (`/portfolio`)

**File:** `frontend/src/pages/Portfolio.tsx`

Displays the user's holdings, performance metrics, risk analysis, and active price alerts.

## API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/portfolio/` | On mount | Holdings list with P&L per stock |
| 2 | GET | `/api/portfolio/performance` | On mount | Aggregate performance metrics |
| 3 | GET | `/api/portfolio/alerts` | On mount | Active price alerts |

## Data Flow

```
Portfolio mounts
    │
    ├─ GET /api/portfolio/
    │      → { totalValue, investedValue, gainPercent,
    │           stocks: [{ symbol, qty, avgBuyPrice, currentPrice,
    │                       gainLoss, gainLossPercent }] }
    │
    ├─ GET /api/portfolio/performance
    │      → { sharpe, maxDrawdown, beta, alpha,
    │           hhiConcentration, valueAtRisk }
    │
    └─ GET /api/portfolio/alerts
           → [{ symbol, alertType, triggerPrice, isActive }]
```

## UI Tabs

| Tab | Content |
|---|---|
| Holdings | Table: Symbol, Qty, Avg Price, CMP, P&L, P&L% |
| Performance | Sharpe ratio, Max Drawdown, Beta, Alpha charts |
| Risk | HHI concentration, VaR, sector allocation breakdown |

---

---

# Predictions (`/predictions`)

**File:** `frontend/src/pages/Predictions.tsx`

Multi-stock AI prediction comparison view with custom timeframe analysis.

## API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/predictions/{symbol}` | On mount (×8, parallel) | AI predictions for 8 predefined stocks |
| 2 | POST | `/api/predictions/{symbol}/custom-analysis` | On "Analyze" button | Custom timeframe prediction analysis |

## Predefined Stocks

The page pre-fetches predictions for these 8 symbols on load:  
`RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, KOTAKBANK, BAJFINANCE, WIPRO`

## Data Flow

```
Predictions mounts
    │
    └─ GET /api/predictions/{symbol}  (×8, parallel)
           → predictions{} map

[Custom Analysis Form]
User selects: symbol, timeframe (1D / 1W / 1M)
    │
    └─ POST /api/predictions/{symbol}/custom-analysis
           body: { timeframe: "1W" }
           → { signal, confidence, targetPrice, stopLoss,
                technicalSummary, sentimentSummary, fundamentalSummary }
```

## UI Layout

- Left panel: 8-stock prediction grid (signal badge + confidence)
- Right panel: Custom analysis form + detailed breakdown
  - Technical tab: RSI, MACD, Bollinger Band summary
  - Sentiment tab: News sentiment score + headlines
  - Fundamental tab: PE ratio, earnings trend, sector comparison

## State

```typescript
predictions: Record<string, Prediction>  // 8-stock map
selectedSymbol: string
analysis: CustomAnalysis | null
timeframe: '1D' | '1W' | '1M'
loadingAnalysis: boolean
```
