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

## AI learning cards (above the tabs)

A two-up section plus the All Stocks directory now sits above the watchlist tabs:

- **System Learning Curve** — three aligned series (cumulative win-rate, trailing
  rolling win-rate, **equity curve**) with a per-source breakdown + **expectancy**,
  a source filter (Paper/Replay/Live), and **vertical event markers** (date-time
  tooltips) so curve moves can be read against system changes. Merged in the same
  card with the **Pattern Recognition Model** sparkline (a continuously-learning
  model trained on patterns only across the full NSE universe, with high-confidence
  accuracy + Train-now). Backed by `/api/ai-engine/learning-curve`,
  `/learning-events`, `/pattern-model/*`.
- **AI Scan Accuracy** — predicted-vs-actual hit-rate per trade day, separate
  **Intraday / Delivery / High-conviction** lines vs a target line.
  Backed by `/api/ai-engine/scan-evaluation`.
- **AI Watchlist → "What changed since the last scan"** — scan-to-scan diff:
  rank movers (with reasons), entrants and drop-offs. Backed by `/scan-diff`.

See [Learning loop](../ai-engine/learning-loop.md) for the AI behind these.

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
- Searchable, filterable, sortable, paginated table
- Columns: Symbol, Company, Sector, Exchange, Price, Change %, Action
- Batch price fetch for visible page only (performance optimization)

**Server-side filters** (re-query + reset to page 1): free-text search (symbol/name),
sector dropdown, and an All / NSE / BSE exchange toggle.

**Client-side sort & filter** (applied to the loaded page — prices are fetched
separately from the symbol metadata, so these operate over the current page):

- **Column sorting** — click any header (Symbol, Company, Sector, Exchange, Price,
  Change %) to toggle ascending/descending; the header shows a ⇅ / ▲ / ▼ indicator.
- **Price range** — Min / Max ₹ inputs.
- **Change direction** — All / ▲ Gainers / ▼ Losers toggle.
- **Clear** — resets the active sort and the price/direction filters at once.

When a client-side filter narrows the page, the stock count reads `N of total`.

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
