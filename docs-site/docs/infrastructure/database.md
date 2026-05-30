---
id: database
title: Database Schema
sidebar_position: 3
---

# Database Schema

## PostgreSQL (TimescaleDB)

Connection: `postgres:5432` · DB: `stock_prediction_db`

| Table | Written by | Read by | Key Columns |
|---|---|---|---|
| `users` | backend/auth.py | backend/auth.py | `id, email, phone, password_hash, broker, broker_api_key, broker_api_secret, is_verified` |
| `ohlcv` | market-data-service | backend/stocks.py, technical-agent, pattern-agent, rl-agent, model-trainer | `symbol, exchange, interval, time, open, high, low, close, volume` |
| `trade_records` | feedback-service | backend/predictions.py, model-trainer | `trade_id, symbol, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, market_context, timestamp_open, timestamp_close, trade_source` |
| `rl_experiences` | feedback-service | model-trainer | `symbol, state, action, reward, next_state, done` |
| `agent_weights` | feedback-service | ensemble-engine | `agent, weight, updated_at` |

The `ohlcv` table is a **TimescaleDB hypertable** partitioned by `time` for fast time-series queries.

## MongoDB

Connection: `mongodb:27017` · DB: `stock_prediction`

| Collection | Written by | Read by | Purpose |
|---|---|---|---|
| `news` | market-data-service | sentiment-agent | Raw news articles keyed by symbol + timestamp |
| `sentiment_scores` | sentiment-agent | backend/stocks.py | FinBERT sentiment results |
| `alerts` | backend/portfolio.py | backend/portfolio.py | User-defined price / pattern alerts |
