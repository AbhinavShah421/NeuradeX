---
id: feedback-trainer
title: Feedback & Model Trainer
sidebar_position: 5
---

# Feedback Service — Port 8012

**Entry point:** [`feedback-service/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/feedback-service/app/main.py)

## HTTP Endpoints

| Method | Path | Called by | Description |
|---|---|---|---|
| `POST` | `/trades` | `backend/app/api/backtest.py` [line 41](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L41) | Ingest backtest trade records |

## RabbitMQ — Consumes

`_consumer_loop()` at [line 164](https://github.com/AbhinavShah421/NeuradeX/blob/main/feedback-service/app/main.py#L164)

| Queue | Exchange | Operations on each message |
|---|---|---|
| `trade.outcomes.feedback` | `trade.outcomes` | 1. Store in `trade_records` · 2. Store RL experience in `rl_experiences` · 3. Update `agent_weights` · 4. Maybe trigger retrain |

## Retraining Trigger

`_maybe_trigger_retrain()` at [line 148](https://github.com/AbhinavShah421/NeuradeX/blob/main/feedback-service/app/main.py#L148)

```python
# Every RETRAIN_THRESHOLD (default 500) trades:
publish(exchange="model.retrain", routing_key="retrain",
        body={"reason": f"retrain_threshold_{N}_reached"})
```

## PostgreSQL — Writes

| Table | Key Columns |
|---|---|
| `trade_records` | `trade_id, symbol, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, timestamp_open, timestamp_close, trade_source` |
| `rl_experiences` | `symbol, state, action, reward, next_state, done` |
| `agent_weights` | `agent, weight, updated_at` |

---

# Model Trainer — Port 8013

**Entry point:** [`model-trainer/`](https://github.com/AbhinavShah421/NeuradeX/blob/main/model-trainer/)

## RabbitMQ — Consumes

| Queue | Exchange | Trigger |
|---|---|---|
| `model.retrain` | `model.retrain` | Published by feedback-service |

## What Gets Retrained

| Model | Data source | Schedule |
|---|---|---|
| RL trading policy | PostgreSQL `rl_experiences` (last `TRAIN_DAYS` = 365 days) | On-demand via message OR every `RETRAIN_SCHEDULE_HOURS` = 24h |
| Technical / pattern models | PostgreSQL `ohlcv` | Same |

## MLflow

All artifacts and metrics → `http://mlflow:5000`  
(`MLFLOW_TRACKING_URI=http://mlflow:5000`)
