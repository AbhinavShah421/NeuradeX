---
id: intro
title: Introduction
sidebar_position: 1
slug: /
---

# NeuradeX — Developer Documentation

NeuradeX is a full-stack, AI-powered intraday stock trading platform built on a microservice architecture. It ingests real-time NSE market data via the Groww API, runs five parallel AI agents, aggregates their signals through a confidence-weighted ensemble engine, validates risk, and executes trades autonomously — all with a live React dashboard, backtesting engine, and Docusaurus dev portal.

---

## What NeuradeX Does

| Capability | Details |
|---|---|
| **Live market data** | Real-time price ticks & news ingestion for 15+ NSE stocks every 60 s |
| **Multi-agent AI** | Technical, Sentiment, Macro, Pattern, and RL agents run in parallel per tick |
| **Ensemble decisions** | Confidence-weighted vote aggregation with dynamic weight learning |
| **Risk management** | ATR-based stop-loss, position-size caps, per-trade risk % enforcement (Java) |
| **Trade execution** | Paper-trading mode (default) and live Groww API order placement (Java) |
| **Backtesting** | Candle-by-candle historical replay with real market data + agent simulation |
| **Strategy backtesting** | SMA Crossover, RSI Mean Reversion, MACD Crossover, Bollinger Bands |
| **Pattern Memory** | Case-based reasoning — fingerprints every setup and recalls how similar ones turned out ([details](./ai-engine/learning-loop)) |
| **Continuous learning** | Every backtest, paper trade and live session trains agent weights + RL + memory |
| **Live Sessions** | Server-backed AI Live Trading & Paper Trading that survive refresh and run in the background ([details](./ai-engine/live-sessions)) |
| **Multi-provider data** | Pluggable Groww / Yahoo / Alpha Vantage with automatic fallback ([details](./ai-engine/data-providers)) |
| **Portfolio tracking** | Real-time P&L, unrealised positions, closed trades, equity curve |
| **Model training** | Scheduled retraining via MLflow + Stable-Baselines3 RL |
| **Feedback loop** | Trade outcomes feed back into ensemble weight updates |

---

## Architecture Overview

```
Groww API / News Sources
        │
  market-data-service (8001)
        │ RabbitMQ: market.tick, news.article
   ┌────┴──────────────────────────────────┐
   │          Five AI Agents               │
   │  technical-agent  (8002) — RSI/MACD  │
   │  sentiment-agent  (8003) — FinBERT   │
   │  macro-agent      (8004) — Macro KPIs│
   │  pattern-agent    (8005) — Candles   │
   │  rl-agent         (8006) — RL Policy │
   └────────────────┬──────────────────────┘
                    │ RabbitMQ: agent.signal
          ensemble-engine (8007)
                    │ RabbitMQ: trade.signal
             risk-engine (8010) ← Java Spring Boot
                    │ RabbitMQ: approved.order
           trade-executor (8011) ← Java Spring Boot
                    │
            Groww Paper/Live API
                    │
          feedback-service (8012)
                    │ RabbitMQ: trade.outcome
             model-trainer (8013)

  stock-scanner    (8014) ── continuous market sweep ──→ Redis: ai_engine:watchlist
  sentiment-service(8016) ── Google-News + LLM ────────→ Redis: ai_engine:sentiment:{SYMBOL}
  autopilot-service(8015) ── paper + backtest training ─→ sessions via backend API
  backend (8000) ← REST API + WebSocket gateway (reads watchlist/sentiment, proxies autopilot)
  frontend (3000) ← React / Vite dashboard
```

---

## Service Registry

| Service | Port | Stack | Role |
|---|---|---|---|
| `backend` | **8000** | Python · FastAPI · Socket.IO | REST API gateway, WebSocket hub, Auth |
| `market-data-service` | **8001** | Python · FastAPI | Groww price tick ingestion, news fetch |
| `technical-agent` | **8002** | Python · FastAPI | RSI, MACD, Bollinger Band, VWAP signals |
| `sentiment-agent` | **8003** | Python · FastAPI · FinBERT | News sentiment scoring |
| `macro-agent` | **8004** | Python · FastAPI | GDP, inflation, FII/DII macro indicators |
| `pattern-agent` | **8005** | Python · FastAPI | Candlestick pattern detection |
| `rl-agent` | **8006** | Python · FastAPI · Stable-Baselines3 | Reinforcement learning trading policy |
| `ensemble-engine` | **8007** | Python · FastAPI | Confidence-weighted signal aggregation |
| `risk-engine` | **8010** | Java 17 · Spring Boot | ATR stop-loss, position-size risk gates |
| `trade-executor` | **8011** | Java 17 · Spring Boot | Paper/live order placement via Groww |
| `feedback-service` | **8012** | Python · FastAPI | Trade outcome recording, weight updates |
| `model-trainer` | **8013** | Python · FastAPI | Scheduled model retraining, MLflow logging |
| `stock-scanner` | **8014** | Python · FastAPI | Independent market sweep — continuously scores the universe for intraday fitness and maintains the AI watchlist |
| `autopilot-service` | **8015** | Python · FastAPI | Auto-trades the watchlist to train the agents — paper (market hours) + backtest (1× replay, off-hours) |
| `sentiment-service` | **8016** | Python · FastAPI · LLM | Google-News + LLM news sentiment per watchlist stock → independent ensemble signal |
| `frontend` | **3000** | React 18 · Vite · TypeScript | Dashboard, backtesting UI, portfolio |
| `docs` | **3001** | Docusaurus 3 | This developer portal |
| `postgres` | **5432** | TimescaleDB (PG 15) | Time-series OHLCV, trades, portfolio |
| `mongodb` | **27017** | MongoDB 6 | News articles, sentiment cache |
| `redis` | **6379** | Redis 7 | Signal cache, session state, macro cache |
| `rabbitmq` | **5672 / 15672** | RabbitMQ 3.12 | Event bus between all services |
| `influxdb` | **8086** | InfluxDB 2 | High-frequency metric storage |
| `elasticsearch` | **9200** | Elasticsearch 8.11 | Log aggregation |
| `kibana` | **5601** | Kibana 8.11 | Log dashboards |
| `mlflow` | **5000** | MLflow 2.10 | Experiment tracking, model registry |

---

## Public Paths (via ngrok / nginx)

| Path | Destination | Description |
|---|---|---|
| `/neuradex` | `frontend:3000` | Main React dashboard |
| `/neuradex/backend/docs` | `backend:8000/docs` | FastAPI Swagger UI |
| `/neuradex/dev/docs/` | `docs:3001` | This developer portal |
| `/neuradex/dev/logs` | `kibana:5601` | Kibana log dashboards |

---

## Tech Stack

### Backend (Python)
- **FastAPI** — async REST + WebSocket
- **Socket.IO** — real-time push to the frontend
- **SQLAlchemy + asyncpg** — async PostgreSQL ORM
- **Motor** — async MongoDB driver
- **aio-pika** — async RabbitMQ client
- **FinBERT** (`ProsusAI/finbert`) — financial news NLP
- **Stable-Baselines3** — PPO reinforcement learning
- **TA-Lib / pandas-ta** — technical indicator computation
- **MLflow** — experiment tracking and model versioning

### Java Services
- **Spring Boot 3** — risk-engine and trade-executor
- **Spring AMQP** — RabbitMQ consumers/publishers
- **Groww REST API** — live and paper order placement

### Frontend
- **React 18 + TypeScript** — component framework
- **Vite** — dev server and bundler
- **Lightweight Charts** — TradingView-style candlestick charts
- **Zustand** — global state (theme, auth)
- **Axios** — HTTP client with interceptors
- **Socket.IO client** — WebSocket subscriptions

### Infrastructure
- **nginx** — reverse proxy, path-based routing
- **Docker Compose** — full-stack local orchestration
- **TimescaleDB** — time-series extension on PostgreSQL
- **ngrok** — public tunnel for remote access

---

## Frontend Pages

| Route | Page | Description |
|---|---|---|
| `/neuradex` | Dashboard | Live index ticker, portfolio summary, top movers, market overview |
| `/neuradex/predictions` | Predictions | AI signal table with confidence scores for all watchlist stocks |
| `/neuradex/portfolio` | Portfolio | Holdings, unrealised P&L, closed trades, equity curve |
| `/neuradex/ai-engine` | AI Live Analysis | Per-stock agent breakdown with indicator values |
| `/neuradex/ai-engine/backtest` | Backtesting & Day Trading | AI Live Trading replay + strategy backtest (persists last result) |
| `/neuradex/ai-engine/paper-trading` | Paper Trading | Server-backed live paper session |
| `/neuradex/ai-engine/sessions` | Live Sessions | Background-persistent sessions — run many, reopen any as a live chart |
| `/neuradex/ai-engine/memory` | Pattern Memory | Memory bank stats + live Agent Learning panel |
| `/neuradex/ai-engine/agents` | Agent Status | Health and last-signal status for each AI agent |
| `/neuradex/models` | Models | MLflow experiment list, model metrics, training history |
| `/neuradex/orders` | Orders | Order blotter with status, fill price, P&L, and per-trade chart |
| `/neuradex/settings` | Settings | Market-data provider priority, enable/disable, API keys |

---

## AI Agent Details

### Technical Agent (8002)
Computes RSI (14), MACD (12/26/9), Bollinger Bands (20, 2σ), VWAP, and SMA crossovers on live 5-minute candles. Emits a directional signal with a `[0, 1]` confidence score.

### Sentiment Agent (8003)
Fetches news headlines for each stock from the last 60 minutes and scores them with `ProsusAI/finbert`. Aggregates sentence-level positive/negative/neutral scores into a single signal.

### Macro Agent (8004)
Reads cached macro indicators (FII/DII flows, RBI repo rate, GDP growth, CPI) from Redis, refreshed every hour. Emits a market-regime signal (risk-on / risk-off).

### Pattern Agent (8005)
Detects Japanese candlestick patterns (Doji, Hammer, Engulfing, Morning Star, etc.) on the last 10 candles using TA-Lib. Outputs a directional confidence proportional to pattern strength.

### RL Agent (8006)
A PPO policy (Stable-Baselines3) trained on 1-year of NSE data via `model-trainer`. State vector includes OHLCV, RSI, MACD, volume ratio, time-of-day. Outputs BUY / HOLD / SELL with a softmax probability.

### Ensemble Engine (8007)
Collects all five agent signals within a 5-second window. Applies per-agent weights (maintained by `feedback-service`) and outputs a final BUY/SELL/HOLD decision with an aggregated confidence score. Only signals above `MIN_CONFIDENCE=0.60` are forwarded to the risk engine.

---

## Data Flow: Tick → Trade

1. `market-data-service` polls Groww every 60 s → publishes `market.tick` to RabbitMQ  
2. All five agents consume `market.tick` in parallel → each publishes `agent.signal`  
3. `ensemble-engine` consumes all `agent.signal` events → aggregates → publishes `trade.signal` if confidence ≥ 0.60  
4. `risk-engine` (Java) consumes `trade.signal` → checks ATR stop-loss, position-size limit, max risk % → publishes `approved.order` or discards  
5. `trade-executor` (Java) consumes `approved.order` → places paper/live order via Groww API → publishes `trade.outcome`  
6. `feedback-service` consumes `trade.outcome` → records result to PostgreSQL → updates ensemble weights in Redis  

---

## Quick Links

- [Continuous Learning & Pattern Memory](./ai-engine/learning-loop) — how every trade trains the agents
- [Live Trading Sessions](./ai-engine/live-sessions) — server-backed, background-persistent sessions
- [Market-Data Providers](./ai-engine/data-providers) — Groww / Yahoo / Alpha Vantage fallback + rate limiting
- [End-to-End Data Flow](./architecture/data-flow) — sequence diagram from tick to trade
- [Dependency Matrix](./architecture/dependency-matrix) — which service talks to which
- [Backend API Reference](./api/auth) — every HTTP endpoint documented
- [RabbitMQ Topology](./infrastructure/rabbitmq) — all exchanges and queue bindings
- [Database Schema](./infrastructure/database) — PostgreSQL tables and MongoDB collections
- [Frontend Overview](./frontend/overview) — component tree and page structure
- [Live API (Swagger)](/neuradex/backend/docs) — interactive FastAPI docs
