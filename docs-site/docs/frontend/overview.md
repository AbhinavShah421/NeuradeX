---
id: overview
title: Frontend Overview
sidebar_label: Overview
---

# Frontend Overview

NeuradeX frontend is a React 18 + TypeScript app built with Vite. It runs on **port 3000** and communicates with the backend API on **port 8000**.

## Tech Stack

| Layer | Library |
|---|---|
| Framework | React 18 + TypeScript |
| Build | Vite |
| State | Zustand (authStore, appStore) |
| HTTP | Axios (with JWT interceptor) |
| Charts | Lightweight-charts |
| Icons | Material Symbols |
| Routing | React Router v6 |

## Route Map

```
/login                    → Login page (public)
/signup                   → Signup page (public)

/ (protected, Layout)
├── /                     → Dashboard
├── /stocks/:symbol       → Stock Detail
├── /portfolio            → Portfolio
├── /predictions          → Predictions
├── /orders               → Orders & Trade History
├── /models               → Model Registry (MLflow)
└── /ai-engine            → AI Engine (nested layout)
    ├── /ai-engine         → AI Engine (Ensemble Analysis)
    ├── /ai-engine/agents  → AI Agent (Ollama LLM)
    ├── /ai-engine/backtest → Backtest
    └── /ai-engine/paper-trading → Paper Trading
```

## Global API Interceptor

**File:** `frontend/src/api.ts`

Every outbound request is automatically enhanced:

```
Request  → attach Authorization: Bearer <JWT from localStorage>
Response → camelCase conversion (snake_case keys from Python backend)
401 err  → clear auth store → redirect to /login
```

## State Management

### authStore (`frontend/src/store/authStore.ts`)
Persisted to `localStorage` key `neuradex-auth`.

| Field | Description |
|---|---|
| `token` | JWT access token |
| `broker` | Selected broker (GROWW / ZERODHA / etc.) |
| `expiresAt` | Token expiry ISO string |
| `isAuthenticated` | Computed boolean |
| `userId` | Numeric user ID from backend |
| `email` | User email |
| `profile` | Full `UserProfile` object from `/api/auth/me` |

### appStore (`frontend/src/store/appStore.ts`)
In-memory only (not persisted).

| Field | Description |
|---|---|
| `stocks[]` | All fetched stocks |
| `selectedStock` | Currently viewed stock |
| `predictions{}` | Symbol → Prediction map |
| `portfolio` | Current user portfolio |
| `theme` | `'light'` \| `'dark'` |

## Key Types (`frontend/src/types/index.ts`)

| Type | Key Fields |
|---|---|
| `Stock` | symbol, price, change, changePercent, volume, peRatio |
| `Prediction` | signal (BUY/SELL/HOLD), confidence, targetPrice, stopLoss, riskRewardRatio |
| `Portfolio` | totalValue, gainPercent, stocks[] (with each holding) |
| `SentimentData` | score, label, newsItems[] |
| `AIAnalysis` | Ollama LLM output with technical indicators |
| `BacktestResult` | equityCurve[], trades[], metrics (Sharpe, maxDrawdown) |
| `LiveSignal` | Real-time BUY/SELL/HOLD with indicators snapshot |
