---
id: portfolio-predictions
title: Portfolio & Predictions
sidebar_label: Portfolio & Predictions
---

# Portfolio (`/portfolio`)

**File:** `frontend/src/pages/Portfolio.tsx`

Live Groww holdings with real prices, plus AI-driven rebalancing, investing and
order management.

## API Calls

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/portfolio/` | Holdings + **real** current prices (Yahoo fallback when Groww live-data is unentitled), P&L, 1D return |
| GET | `/api/portfolio/optimize?refresh=` | AI rebalancing plan (cached, scan-keyed) |
| GET | `/api/portfolio/invest-plan?amount=` | AI allocation of an amount across the best picks |
| GET | `/api/orders/` · POST `/api/orders/cancel` | Live Groww order book + cancel |
| POST | `/api/orders/` | Place a Buy/Sell/Swap leg (protective LIMIT) |
| GET | `/api/ai-engine/scan-status` | Shared scan status (Rescan button) |
| GET | `/api/portfolio/performance` · `/api/portfolio/alerts` | Metrics + alerts |

## UI Tabs

| Tab | Content |
|---|---|
| **Holdings** | Symbol, Qty, Avg, CMP (live), P&L, P&L% |
| **Performance** | Sharpe, Max Drawdown, returns |
| **Risk** | HHI concentration, VaR, sector breakdown |
| **AI Optimize** | Scan-keyed rebalancing plan: per-holding AI signal + EXIT/TRIM/HOLD/ADD + target weight; for at-risk holdings an AI **alternative** and a one-click **Swap** (sell→buy, with the basis shown); live "Today's Orders" panel with cancel |
| **AI Invest** | Enter an amount → AI divides it across the best A/B picks (conviction-weighted) as protective LIMIT buys; per-stock Buy + **Invest all** |
| **AI Advisor** | Portfolio-vs-NIFTY alpha (1M/3M/1Y) + an LLM insights feed synthesising health, sector, benchmark and tax |
| **AI Risk Lab** | True (correlation) diversification score + hidden-concentration pairs; scenario stress-test (market/sector/rate shocks + fragile names); ATR smart-exit levels (stop/target/trail + downgrade flags); dividend income forecast |
| **Health Score** | 0-100 health gauge + factor bars (diversification, concentration, sector, quality, performance, drawdown) + issues & fixes |
| **Sector Exposure** | Donut of current sector weights vs an AI-favoured target, over/under-exposure bars + rebalance moves |
| **AI Funds** | AI-scanned mutual-fund-style stock baskets (Top Picks, Sector Leaders, Momentum, Balanced, High-Conviction) with one-click invest |
| **Goal Planner** | Goal → required SIP / projected corpus + growth chart + risk-based allocation; **"Find my risk"** questionnaire |
| **Tax Harvest** | Unrealised gains/losses, loss-harvest candidates + estimated tax saved, ELSS/80C tips |

See [Portfolio API](../api/portfolio.md#ai-portfolio-intelligence) for the
endpoints behind the AI tabs.

Every page (Holdings header, Predictions, Dashboard) shares one **ScanControl**
(scan status + Rescan) backed by [`scanStore`](https://github.com/AbhinavShah421/NeuradeX/blob/main/frontend/src/stores/scanStore.ts);
the Rescan button is disabled on all pages while a sweep runs. Orders are placed
only behind an explicit confirmation modal; the order book stays in sync with
Groww (poll + refresh on tab focus).

---

---

# Predictions (`/predictions`) — AI Stock Rankings

**File:** `frontend/src/pages/Predictions.tsx`

A ranked board of **every AI-scanned stock**, sourced from the full-market
scanner. Filter by **Top 10 / 20 / 50 / 100 / All**, and click any row to see
**why it earned its rank**.

## API Calls

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/ai-engine/ranked?limit=N` | Ranked board (top N) with full per-stock evidence + news |
| GET | `/api/ai-engine/scan-status` | Shared scan status / Rescan (via ScanControl) |

## UI

- **Filter bar:** Top 10 / 20 / 50 / 100 / All + `scanned/universe · ranked · time` + shared **Rescan**.
- **Ranked table:** `#rank · symbol/company · action · grade · win% · signal score · momentum · price`.
- **Detail modal** (click a row) — *why this rank*: signal/rank score, win probability, factors aligned (x/6), news boost; the **agent factor vote** (trend / momentum / MACD / volume / regime / RSI — ✓/✗); LLM **news sentiment**; the full **market-indicator evidence**; and the reasoning string.

When a centralized scan finishes, the board auto re-pulls so it reflects the
latest ranks. Backed by the scanner's `ai_engine:ranked` board (see
[Stock Scanner](../microservices/stock-scanner.md#ranked-board-predictions-page)).
