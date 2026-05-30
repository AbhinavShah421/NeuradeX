---
id: orders-models
title: Orders & Model Registry
sidebar_label: Orders & Models
---

# Orders (`/orders`)

**File:** `frontend/src/pages/Orders.tsx`

Trade history aggregated across all trading modes: Live, Paper, and Backtest.

## API Calls

> Orders page calls the **feedback service** directly on port 8012 (not the main backend).

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `http://localhost:8012/stats` | On mount | Aggregate trade statistics |
| 2 | GET | `http://localhost:8012/trades` | On mount | Full trade history list |

## Data Flow

```
Orders mounts
    │
    ├─ GET http://localhost:8012/stats
    │      → {
    │           total_trades: 142,
    │           win_rate: 0.63,
    │           total_pnl: 18430.50,
    │           avg_return_per_trade: 129.79,
    │           sharpe: 1.42
    │         }
    │
    └─ GET http://localhost:8012/trades
           → [
               {
                 id, symbol, side, entry_price, exit_price,
                 quantity, pnl, mode: "PAPER" | "LIVE" | "BACKTEST",
                 entry_time, exit_time,
                 execution_trace: [...6-step pipeline]
               }
             ]
```

## UI Features

- Filter by mode: ALL / LIVE / PAPER / BACKTEST
- Click any trade → open **Execution Trace Modal**

### Execution Trace Modal

Shows the full 6-step decision pipeline for that trade:

```
Step 1: Market Data Fetch   → tick received at HH:MM:SS
Step 2: Technical Analysis  → RSI 58, MACD bullish crossover
Step 3: Sentiment           → score 0.72 (POSITIVE)
Step 4: Ensemble Vote       → 4/5 agents voted BUY
Step 5: Risk Validation     → position size 5 shares (2% portfolio)
Step 6: Order Execution     → filled @ 2834.50 in 143ms
```

---

# Model Registry (`/models`)

**File:** `frontend/src/pages/ModelRegistry.tsx`

Browse all ML models registered in MLflow with their performance metrics.

## API Calls

> Calls MLflow via the backend proxy (not MLflow directly).

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/mlflow/registered-models/search` | On mount | List all registered models |
| 2 | GET | `/api/mlflow/runs/get` | Per model | Fetch run metrics for each model |

## Data Flow

```
ModelRegistry mounts
    │
    └─ GET /api/mlflow/registered-models/search
           query: max_results=50
           → { registered_models: [{ name, latest_versions: [{ run_id }] }] }

For each model's latest run_id:
    │
    └─ GET /api/mlflow/runs/get?run_id={run_id}
           → {
               run: {
                 data: {
                   metrics: { accuracy, sharpe_ratio, max_drawdown, ... },
                   params:  { model_type, symbol, train_days, ... },
                   tags:    { mlflow.runName, ... }
                 }
               }
             }
```

## Displayed Models

| Model Name (MLflow) | Trained By | Metrics Shown |
|---|---|---|
| `technical-signal-*` | technical-agent | Accuracy, F1 |
| `sentiment-scorer-*` | sentiment-agent | Accuracy |
| `pattern-classifier-*` | pattern-agent | Accuracy, Precision |
| `rl-trading-policy` | rl-agent / model-trainer | Sharpe, Max Drawdown |
| `ensemble-weights-*` | ensemble-engine | Win Rate, Avg Return |

## State

```typescript
models: RegisteredModel[]
metrics: Record<string, RunMetrics>  // run_id → metrics
loading: boolean
```
