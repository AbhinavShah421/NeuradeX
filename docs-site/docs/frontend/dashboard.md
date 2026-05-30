---
id: dashboard
title: Dashboard
sidebar_label: Dashboard
---

# Dashboard (`/`)

**File:** `frontend/src/pages/Dashboard.tsx`

The main landing page after login. Shows an AI-curated watchlist and a paginated directory of all stocks.

## API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/stocks/` | On mount | Fetch all tracked stocks |
| 2 | GET | `/api/predictions/{symbol}` | After stocks load (parallel) | AI prediction for each stock |
| 3 | GET | `/api/predictions/accuracy/stats` | On mount (parallel) | Model accuracy statistics |
| 4 | GET | `/api/stocks/directory/list` | Directory tab, on page/search change | Paginated stock list with search & filter |
| 5 | POST | `/api/stocks/directory/prices` | After directory list loads | Batch-fetch live prices for current page |

## Data Flow

```
Dashboard mounts
    │
    ├─ GET /api/stocks/                    → stocks[] (watchlist)
    │       │
    │       └─ for each stock:
    │          GET /api/predictions/{symbol} → predictions{} map
    │
    └─ GET /api/predictions/accuracy/stats  → accuracyStats
                                              (accuracy%, total predictions)

[Tab: All Stocks Directory]
    │
    ├─ GET /api/stocks/directory/list      → paginated stocks list
    │     query: page, page_size, search, sector, sort_by
    │
    └─ POST /api/stocks/directory/prices   → live prices for page
          body: { symbols: string[] }
```

## UI Tabs

### Tab 1: AI Watchlist
- Displays all stocks from watchlist with:
  - Real-time price + change%
  - AI signal badge (BUY / SELL / HOLD)
  - Confidence bar
  - Target price
- Model accuracy stats banner at top
- Click any stock → navigate to `/stocks/:symbol`

### Tab 2: All Stocks Directory
- Searchable, filterable, paginated table
- Columns: Symbol, Company, Sector, Price, Change%, Volume
- Batch price fetch for visible page only (performance optimization)

## State

```typescript
stocks: Stock[]          // from GET /api/stocks/
predictions: Record<string, Prediction>  // symbol → prediction
accuracyStats: { accuracy: number; totalPredictions: number }
activeTab: 'watchlist' | 'directory'

// Directory tab
directoryStocks: Stock[]
directoryPage: number
directorySearch: string
directoryPrices: Record<string, number>
```
