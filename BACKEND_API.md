# NeuradeX — Backend API & Microservice Communication Reference

> **Purpose:** Complete reference of every HTTP endpoint, RabbitMQ message flow, Redis key, and inter-service call.  
> File paths and line numbers are anchored to the current source so you can jump directly to the code.

---

## Table of Contents

1. [Service Registry](#1-service-registry)
2. [Full Data Flow (End-to-End)](#2-full-data-flow-end-to-end)
3. [Backend Monolith (Port 8000)](#3-backend-monolith-port-8000)
   - [Auth](#31-auth--apiauth)
   - [Stocks](#32-stocks--apistocks)
   - [Predictions](#33-predictions--apipredictions)
   - [Portfolio](#34-portfolio--apiportfolio)
   - [Orders](#35-orders--apiorders)
   - [Risk Analytics](#36-risk-analytics--apirisk)
   - [AI Agent](#37-ai-agent--apiagent)
   - [Backtest](#38-backtest--apibacktest)
   - [Paper Trading](#39-paper-trading--apipaper-trading)
   - [AI Engine](#310-ai-engine--apiai-engine)
   - [System](#311-system-routes)
   - [WebSocket](#312-websocket)
4. [Market Data Service (Port 8001)](#4-market-data-service-port-8001)
5. [Agent Services (Ports 8002–8006)](#5-agent-services-ports-80028006)
   - [Technical Agent (8002)](#51-technical-agent-port-8002)
   - [Sentiment Agent (8003)](#52-sentiment-agent-port-8003)
   - [Macro Agent (8004)](#53-macro-agent-port-8004)
   - [Pattern Agent (8005)](#54-pattern-agent-port-8005)
   - [RL Agent (8006)](#55-rl-agent-port-8006)
6. [Ensemble Engine (Port 8007)](#6-ensemble-engine-port-8007)
7. [Risk Engine (Port 8010)](#7-risk-engine-port-8010)
8. [Trade Executor (Port 8011)](#8-trade-executor-port-8011)
9. [Feedback Service (Port 8012)](#9-feedback-service-port-8012)
10. [Model Trainer (Port 8013)](#10-model-trainer-port-8013)
11. [RabbitMQ Topology](#11-rabbitmq-topology)
12. [Redis Key Reference](#12-redis-key-reference)
13. [Database Schema Overview](#13-database-schema-overview)
14. [Inter-Service HTTP Calls](#14-inter-service-http-calls)
15. [Dependency Matrix](#15-dependency-matrix)

---

## 1. Service Registry

| Service | Port | Language | Infrastructure |
|---|---|---|---|
| `backend` | **8000** | Python / FastAPI | PostgreSQL · MongoDB · Redis · RabbitMQ · Elasticsearch |
| `market-data-service` | **8001** | Python / FastAPI | PostgreSQL · MongoDB · Redis · RabbitMQ |
| `technical-agent` | **8002** | Python / FastAPI | PostgreSQL · RabbitMQ |
| `sentiment-agent` | **8003** | Python / FastAPI | MongoDB · RabbitMQ |
| `macro-agent` | **8004** | Python / FastAPI | Redis · RabbitMQ |
| `pattern-agent` | **8005** | Python / FastAPI | PostgreSQL · RabbitMQ |
| `rl-agent` | **8006** | Python / FastAPI | PostgreSQL · Redis · RabbitMQ |
| `ensemble-engine` | **8007** | Python / FastAPI | PostgreSQL · Redis · RabbitMQ |
| `risk-engine` | **8010** | Java / Spring Boot | RabbitMQ |
| `trade-executor` | **8011** | Java / Spring Boot | RabbitMQ |
| `feedback-service` | **8012** | Python / FastAPI | PostgreSQL · Redis · RabbitMQ |
| `model-trainer` | **8013** | Python / FastAPI | PostgreSQL · RabbitMQ · MLflow (5000) |
| `frontend` | **3000** | React / Vite | HTTP → backend:8000 |

---

## 2. Full Data Flow (End-to-End)

```
  [Groww API / Yahoo Finance / NewsAPI]
              │
              ▼
  ┌──────────────────────┐   publish: market.data (fanout)
  │  market-data-service  │ ──────────────────────────────────────────┐
  │       :8001           │                                           │
  └──────────────────────┘                                           │
        │ Redis: tick:{symbol}                                        │
        │                                                             ▼
        │                           ┌────────────┬────────────┬────────────┬──────────────┐
        │                           │ technical  │ sentiment  │   macro    │   pattern    │  rl-agent
        │                           │  :8002     │   :8003    │   :8004    │   :8005      │  :8006
        │                           │ (technical)│ (sentiment)│  (macro)   │  (pattern)   │  (rl)
        │                           └─────┬──────┴─────┬──────┴─────┬──────┴──────┬───────┴────┬──────┘
        │                                 │             │            │             │             │
        │                                 └─────────────┴────────────┴─────────────┴─────────────┘
        │                                              publish: agent.signals (direct)
        │                                                           │
        │                                                           ▼
        │                                            ┌──────────────────────┐
        │                                            │   ensemble-engine    │
        │                                            │        :8007         │
        │                                            │  (waits for 5 agents)│
        │                                            └──────────┬───────────┘
        │                                                        │ publish: ensemble.decision
        │                                              Redis: ensemble:{symbol} (TTL 300s)
        │                                                        │
        │                                         ┌──────────────┴──────────────┐
        │                                         ▼                             ▼
        │                              ┌────────────────────┐     ┌────────────────────┐
        │                              │    risk-engine     │     │   trade-executor   │
        │                              │       :8010        │     │       :8011        │
        │                              │  (Java/Spring Boot)│     │  (Java/Spring Boot)│
        │                              └─────────┬──────────┘     └────────────────────┘
        │                                        │ publish: risk.validated
        │                                        ▼
        │                              ┌────────────────────┐
        │                              │   trade-executor   │  ──→  Groww API (live)
        │                              │       :8011        │       or paper trading
        │                              └─────────┬──────────┘
        │                                        │ publish: trade.outcomes (fanout)
        │                              ┌─────────┴──────────┐
        │                              ▼                     ▼
        │                   ┌──────────────────┐  ┌──────────────────┐
        │                   │ feedback-service  │  │    rl-agent      │
        │                   │      :8012        │  │     :8006        │
        │                   │ (updates weights, │  │ (updates policy  │
        │                   │  stores trades)   │  │  from outcome)   │
        │                   └────────┬──────────┘  └──────────────────┘
        │                            │ publish: model.retrain (every N trades)
        │                            ▼
        │                   ┌──────────────────┐
        │                   │  model-trainer   │  ──→  MLflow :5000
        │                   │     :8013        │
        │                   └──────────────────┘
        │
        ▼
  [backend :8000]  ←→  Frontend :3000  (HTTP REST + WebSocket)
  Reads Redis/PostgreSQL/MongoDB for API responses
  HTTP POST → feedback-service:8012/trades  (backtest results)
```

---

## 3. Backend Monolith (Port 8000)

**Entry point:** [`backend/app/main.py`](backend/app/main.py)  
**Command:** `python -m uvicorn app.main:app_sio --host 0.0.0.0 --port 8000 --reload`

All routes are registered in `main.py` via `app.include_router(...)`.

---

### 3.1 Auth — `/api/auth`

**File:** [`backend/app/api/auth.py`](backend/app/api/auth.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/signup/send-otp` | [123](backend/app/api/auth.py#L123) | Send OTP to phone/email for new user signup |
| `POST` | `/api/auth/signup/verify-otp` | [160](backend/app/api/auth.py#L160) | Verify OTP, return a short-lived token |
| `POST` | `/api/auth/signup/complete` | [170](backend/app/api/auth.py#L170) | Complete registration (name, password, broker credentials) |
| `POST` | `/api/auth/login` | [228](backend/app/api/auth.py#L228) | Email + password login, returns JWT |
| `GET` | `/api/auth/me` | [268](backend/app/api/auth.py#L268) | Returns current authenticated user profile |
| `GET` | `/api/auth/profile` | [280](backend/app/api/auth.py#L280) | Extended profile with broker linkage status |
| `POST` | `/api/auth/logout` | [368](backend/app/api/auth.py#L368) | Invalidates session / token |
| `GET` | `/api/auth/groww/status` | [375](backend/app/api/auth.py#L375) | Check if Groww broker account is linked and token is valid |
| `POST` | `/api/auth/groww/refresh` | [395](backend/app/api/auth.py#L395) | Refresh Groww OAuth token |
| `PUT` | `/api/auth/groww/credentials` | [412](backend/app/api/auth.py#L412) | Update stored Groww API key / secret |

**Auth flow:**
```
Client
  │─ POST /signup/send-otp ──→ OTP generated, stored in Redis (TTL 5 min)
  │─ POST /signup/verify-otp ─→ OTP validated, temp token issued
  │─ POST /signup/complete ───→ User row inserted into PostgreSQL `users` table
  │─ POST /login ─────────────→ Password verified, JWT returned
  │─ GET  /me (Bearer JWT) ───→ Decoded from JWT, user fetched from PostgreSQL
```

**External calls:** OTP delivery via [`backend/app/utils/otp_service.py`](backend/app/utils/otp_service.py)

---

### 3.2 Stocks — `/api/stocks`

**File:** [`backend/app/api/stocks.py`](backend/app/api/stocks.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/stocks/` | [87](backend/app/api/stocks.py#L87) | List all tracked stocks with current price (from Redis `tick:{symbol}`) |
| `GET` | `/api/stocks/{symbol}` | [136](backend/app/api/stocks.py#L136) | Single stock detail — price, change, volume |
| `GET` | `/api/stocks/{symbol}/candlesticks` | [159](backend/app/api/stocks.py#L159) | OHLCV candles from PostgreSQL `ohlcv` table; query params: `interval`, `limit` |
| `GET` | `/api/stocks/{symbol}/sentiment` | [227](backend/app/api/stocks.py#L227) | Latest sentiment score from MongoDB `sentiment_scores` |
| `GET` | `/api/stocks/directory/list` | [248](backend/app/api/stocks.py#L248) | Full NSE/BSE stock directory from [`backend/app/data/stocks_master.py`](backend/app/data/stocks_master.py) |
| `POST` | `/api/stocks/directory/prices` | [295](backend/app/api/stocks.py#L295) | Bulk price lookup for a list of symbols |

**Data sources per endpoint:**

```
/stocks/           ── Redis: tick:{symbol}  (written by market-data-service)
/stocks/{symbol}   ── Redis: tick:{symbol}
/candlesticks      ── PostgreSQL: ohlcv table  (written by market-data-service)
/sentiment         ── MongoDB: sentiment_scores  (written by sentiment-agent)
/directory/list    ── Static master list in backend/app/data/
/directory/prices  ── Redis: tick:{symbol}  (batch read)
```

---

### 3.3 Predictions — `/api/predictions`

**File:** [`backend/app/api/predictions.py`](backend/app/api/predictions.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/predictions/{symbol}` | [34](backend/app/api/predictions.py#L34) | Latest ensemble prediction for a symbol; reads Redis `ensemble:{symbol}` |
| `POST` | `/api/predictions/{symbol}/custom-analysis` | [107](backend/app/api/predictions.py#L107) | Trigger on-demand analysis for a symbol (calls ensemble-engine HTTP or reads cached) |
| `GET` | `/api/predictions/{symbol}/history` | [156](backend/app/api/predictions.py#L156) | Historical prediction records from PostgreSQL |
| `GET` | `/api/predictions/accuracy/stats` | [186](backend/app/api/predictions.py#L186) | Model accuracy metrics from PostgreSQL `trade_records` |

**Data flow:**
```
GET /predictions/{symbol}
  └─ Redis GET ensemble:{symbol}
       ├─ HIT  → return cached ensemble decision (set by ensemble-engine)
       └─ MISS → return empty / trigger custom-analysis
```

---

### 3.4 Portfolio — `/api/portfolio`

**File:** [`backend/app/api/portfolio.py`](backend/app/api/portfolio.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/portfolio/` | [95](backend/app/api/portfolio.py#L95) | User's holdings — fetched from Groww API via [`backend/app/utils/groww_client.py`](backend/app/utils/groww_client.py) |
| `POST` | `/api/portfolio/add` | [158](backend/app/api/portfolio.py#L158) | Manually add a holding to tracked portfolio |
| `GET` | `/api/portfolio/performance` | [174](backend/app/api/portfolio.py#L174) | P&L and performance metrics |
| `GET` | `/api/portfolio/alerts` | [193](backend/app/api/portfolio.py#L193) | List active price/pattern alerts from MongoDB `alerts` |
| `POST` | `/api/portfolio/alerts` | [207](backend/app/api/portfolio.py#L207) | Create a new alert |

**External call:** Groww broker API (`groww_client.py`) for live holdings data.

---

### 3.5 Orders — `/api/orders`

**File:** [`backend/app/api/orders.py`](backend/app/api/orders.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `POST` | `/api/orders/` | [48](backend/app/api/orders.py#L48) | Place an order via Groww API; stores record in PostgreSQL |
| `GET` | `/api/orders/` | [147](backend/app/api/orders.py#L147) | List historical orders for the authenticated user |

**External call:** Groww broker API (`groww_client.py`) for order placement.

---

### 3.6 Risk Analytics — `/api/risk`

**File:** [`backend/app/api/risk.py`](backend/app/api/risk.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/risk/var` | [205](backend/app/api/risk.py#L205) | Value-at-Risk calculation for user's portfolio |
| `GET` | `/api/risk/stress-test` | [269](backend/app/api/risk.py#L269) | Stress-test portfolio against historical shock scenarios |
| `GET` | `/api/risk/factors` | [360](backend/app/api/risk.py#L360) | Factor exposure breakdown (beta, sector, volatility) |
| `GET` | `/api/risk/optimization` | [507](backend/app/api/risk.py#L507) | Mean-variance optimal portfolio weights |
| `GET` | `/api/risk/optimization/analyze` | [619](backend/app/api/risk.py#L619) | Explain current vs optimal allocation gap |

**Data sources:** PostgreSQL `ohlcv` table for historical prices, Groww API for current holdings.

---

### 3.7 AI Agent — `/api/agent`

**File:** [`backend/app/api/agent.py`](backend/app/api/agent.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/agent/stocks` | [357](backend/app/api/agent.py#L357) | List of stocks currently being analyzed by agents |
| `GET` | `/api/agent/models` | [385](backend/app/api/agent.py#L385) | Status and accuracy of each agent model |
| `POST` | `/api/agent/analyze/{symbol}` | [407](backend/app/api/agent.py#L407) | Trigger synchronous agent analysis for a symbol; aggregates all 5 agent signals inline |

**Agent classes used inline:**  
[`backend/app/agents/technical.py`](backend/app/agents/technical.py) · [`backend/app/agents/sentiment.py`](backend/app/agents/sentiment.py) · [`backend/app/agents/pattern.py`](backend/app/agents/pattern.py) · [`backend/app/agents/momentum.py`](backend/app/agents/momentum.py) · [`backend/app/agents/volatility.py`](backend/app/agents/volatility.py) · [`backend/app/agents/rl_agent.py`](backend/app/agents/rl_agent.py) · [`backend/app/agents/ensemble.py`](backend/app/agents/ensemble.py)

---

### 3.8 Backtest — `/api/backtest`

**File:** [`backend/app/api/backtest.py`](backend/app/api/backtest.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/backtest/strategies` | [456](backend/app/api/backtest.py#L456) | List available backtest strategy configs |
| `POST` | `/api/backtest/run` | [461](backend/app/api/backtest.py#L461) | Run a full historical backtest; fires-and-forgets trade records to `feedback-service` |
| `GET` | `/api/backtest/live-signal/{symbol}` | [546](backend/app/api/backtest.py#L546) | Current live signal for a symbol using backtest strategy |
| `POST` | `/api/backtest/day-autopilot` | [871](backend/app/api/backtest.py#L871) | Run full-day autopilot simulation |
| `GET` | `/api/backtest/intraday-candles/{symbol}` | [1104](backend/app/api/backtest.py#L1104) | 1-min intraday candles for a symbol |
| `POST` | `/api/backtest/agent-step` | [1177](backend/app/api/backtest.py#L1177) | Advance backtest by one agent decision step |
| `POST` | `/api/backtest/progressive/start` | [1392](backend/app/api/backtest.py#L1392) | Start a progressive (streaming) backtest session |
| `POST` | `/api/backtest/progressive/step` | [1512](backend/app/api/backtest.py#L1512) | Advance progressive backtest one candle at a time |

**Inter-service call from backtest:**
```python
# backend/app/api/backtest.py : line 27
FEEDBACK_SERVICE_URL = "http://feedback-service:8012"

# line 41 — fire-and-forget HTTP POST
asyncio.create_task(
    httpx.post(f"{FEEDBACK_SERVICE_URL}/trades", json=trade_records)
)
```

---

### 3.9 Paper Trading — `/api/paper-trading`

**File:** [`backend/app/api/paper_trading.py`](backend/app/api/paper_trading.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/api/paper-trading/status` | [498](backend/app/api/paper_trading.py#L498) | Current paper trading session state (balance, P&L, open positions) |
| `POST` | `/api/paper-trading/start` | [523](backend/app/api/paper_trading.py#L523) | Start a new paper trading session with initial capital |
| `POST` | `/api/paper-trading/step` | [646](backend/app/api/paper_trading.py#L646) | Advance session one tick using agent decisions |
| `GET` | `/api/paper-trading/tick/{symbol}` | [780](backend/app/api/paper_trading.py#L780) | Get the next simulated tick for a symbol |
| `POST` | `/api/paper-trading/place-order` | [968](backend/app/api/paper_trading.py#L968) | Place a simulated order (no real money) |

---

### 3.10 AI Engine — `/api/ai-engine`

**File:** [`backend/app/api/ai_engine.py`](backend/app/api/ai_engine.py)

LLM-backed conversational analysis endpoint.  
**MLflow proxy** routes are also registered from this file — forwarded to `mlflow:5000`.

**File:** [`backend/app/api/mlflow_proxy.py`](backend/app/api/mlflow_proxy.py) — Proxies `/api/mlflow/*` requests to the internal MLflow server.

---

### 3.11 System Routes

**File:** [`backend/app/main.py`](backend/app/main.py)

| Method | Path | Line | Description |
|--------|------|------|-------------|
| `GET` | `/` | [100](backend/app/main.py#L100) | Root — returns service name and version |
| `GET` | `/health` | [105](backend/app/main.py#L105) | Health check — verifies DB and Redis connectivity |

#### Docker control panel — `/api/system`

**File:** [`backend/app/api/system.py`](backend/app/api/system.py)

Powers the floating **system-status panel** in the frontend. Talks to the Docker
Engine API over the mounted unix socket (`/var/run/docker.sock`) using an httpx
UDS transport — no extra Python package. Scoped to project containers only
(name prefix `stock-prediction-`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/system/services` | List every project container with `state`, `status`, `health`, live `cpu_pct` / `mem_used_mb`, and recent-log `log_severity` (`error`/`warning`/`ok`). Returns aggregate `totals`. **Stale-while-revalidate**: serves a cached snapshot instantly and refreshes in the background; `?fresh=true` forces a synchronous recompute (used by the manual Refresh). `?stats=false` returns the container list only (fast, no enrichment). |
| `POST` | `/api/system/services/{name}/{action}` | `action` ∈ `start` \| `stop` \| `restart`. Refuses to stop/restart `stock-prediction-backend` (can't act on the container serving the request). |
| `POST` | `/api/system/restart-all` | Restart all running project containers in parallel, skipping the backend. |
| `GET` | `/api/system/services/{name}/logs?tail=N` | Interactive, self-contained HTML log viewer (opened in a new tab). Client-side filtering: search highlight, level chips (Error/Warning/Info/Trace), from/to time range, tail-size selector (200–5000), auto-refresh (Off–60s, persisted), Clear view, Reset, and **Delete logs**. |
| `DELETE` | `/api/system/services/{name}/logs` | Truncate the container's json-log file(s) to zero bytes (non-disruptive "clear"). Once empty, the severity scan returns `ok` and the panel's logs icon turns green. |

**Requirements (docker-compose `backend` service):**
- `/var/run/docker.sock:/var/run/docker.sock` — Engine API access (list/logs/control/stats)
- `/var/lib/docker/containers:/var/lib/docker/containers` — to truncate json-log files for the Delete-logs action

> ⚠️ These endpoints are **unauthenticated** (same posture as `/api/agent/services/health`) and the Docker socket is root-equivalent on the host. Intended for the operator's own local/personal deployment.

---

### 3.12 WebSocket

**File:** [`backend/app/websocket/socket_manager.py`](backend/app/websocket/socket_manager.py)

The backend uses **Socket.IO** (`app_sio` in `main.py`). The frontend connects via `VITE_SOCKET_URL=http://localhost:8000`.

| Event (server → client) | Description |
|---|---|
| `tick_update` | Real-time price tick for subscribed symbols |
| `prediction_update` | New ensemble decision pushed when Redis key changes |
| `alert_triggered` | User alert fired |

---

## 4. Market Data Service (Port 8001)

**Entry point:** [`market-data-service/app/main.py`](market-data-service/app/main.py)

### HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/tick/{symbol}` | Read latest tick from Redis `tick:{symbol}` |

### Background Jobs

| Loop | File | What it does |
|---|---|---|
| Price tick loop | [`market-data-service/app/services/`](market-data-service/app/services/) | Polls Groww API / Yahoo Finance every `TICK_INTERVAL_SECONDS` (default 60s); writes Redis; publishes to `market.data` exchange |
| News ingestion loop | same | Polls NewsAPI every `NEWS_INTERVAL_SECONDS` (default 300s); stores in MongoDB `news`; publishes `news_ingested` notification |

### RabbitMQ — Publishes

**File:** [`market-data-service/app/services/rabbitmq_publisher.py`](market-data-service/app/services/rabbitmq_publisher.py)

| Exchange | Routing Key | Payload | Line |
|---|---|---|---|
| `market.data` | _(fanout, no key)_ | `{symbol, price, timestamp, open, high, low, close, volume, exchange}` | [41–56](market-data-service/app/services/rabbitmq_publisher.py#L41) |
| `notifications` | _(fanout, no key)_ | `{event: "news_ingested", article_count}` | [58–72](market-data-service/app/services/rabbitmq_publisher.py#L58) |

### Redis — Writes

**File:** [`market-data-service/app/services/redis_writer.py`](market-data-service/app/services/redis_writer.py)

| Key | Value | TTL |
|---|---|---|
| `tick:{SYMBOL}` | `{ltp, volume, timestamp, open, high, low, close}` | 120 s |
| `candle:{SYMBOL}:{interval}` | JSON array of recent candles | 300 s |

---

## 5. Agent Services (Ports 8002–8006)

All five agents share the same pattern:

```
market.data exchange (fanout)
  │
  ├─ market.data.technical  ──▶  technical-agent  :8002
  ├─ market.data.sentiment  ──▶  sentiment-agent  :8003
  ├─ market.data.macro      ──▶  macro-agent      :8004
  ├─ market.data.pattern    ──▶  pattern-agent    :8005
  └─ market.data.rl         ──▶  rl-agent         :8006
                                        │
                                        ▼
                              agent.signals exchange (direct)
                              routing_key = agent_name
```

**Signal message shape (all agents publish to `agent.signals`):**
```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "agent": "technical",
  "signal": "BUY",
  "confidence": 0.78,
  "reasoning": "RSI oversold, MACD crossover",
  "indicators": { ... },
  "model_votes": { ... }
}
```

---

### 5.1 Technical Agent (Port 8002)

**Consumer:** [`technical-agent/app/consumer.py`](technical-agent/app/consumer.py) — `start_consuming()` at [line 66](technical-agent/app/consumer.py#L66)

| Consumes | Publishes | Data source |
|---|---|---|
| Queue: `market.data.technical` | Exchange: `agent.signals` · routing_key: `technical` | PostgreSQL `ohlcv` (CANDLE_HISTORY_LIMIT=200 candles) |

**Signal computation:** RSI, MACD, Bollinger Bands, moving averages — [`technical-agent/app/`](technical-agent/app/)

**Publish call:** [consumer.py line 80–87](technical-agent/app/consumer.py#L80)

---

### 5.2 Sentiment Agent (Port 8003)

**Consumer file:** [`sentiment-agent/app/`](sentiment-agent/app/)

| Consumes | Publishes | Model |
|---|---|---|
| Queue: `market.data.sentiment` | Exchange: `agent.signals` · routing_key: `sentiment` | FinBERT (`ProsusAI/finbert`) — scores news from MongoDB `news` within last `SENTIMENT_WINDOW_MINUTES` (60 min) |

---

### 5.3 Macro Agent (Port 8004)

**Consumer file:** [`macro-agent/app/`](macro-agent/app/)

| Consumes | Publishes | Data source |
|---|---|---|
| Queue: `market.data.macro` | Exchange: `agent.signals` · routing_key: `macro` | External macro data APIs; cached in Redis; refreshed every `MACRO_REFRESH_SECONDS` (3600s) |

---

### 5.4 Pattern Agent (Port 8005)

**Consumer file:** [`pattern-agent/app/`](pattern-agent/app/)

| Consumes | Publishes | Data source |
|---|---|---|
| Queue: `market.data.pattern` | Exchange: `agent.signals` · routing_key: `pattern` | PostgreSQL `ohlcv` (CANDLE_HISTORY_LIMIT=100 candles) — detects candlestick patterns |

---

### 5.5 RL Agent (Port 8006)

**Consumer file:** [`rl-agent/app/`](rl-agent/app/)

| Consumes | Publishes | Model |
|---|---|---|
| Queue: `market.data.rl` | Exchange: `agent.signals` · routing_key: `rl` | Reinforcement learning policy loaded from MLflow (`POLICY_MODEL_NAME="rl-trading-policy"`) |

Also **consumes** `trade.outcomes.rl` queue to update the policy from real trade feedback.

---

## 6. Ensemble Engine (Port 8007)

**Entry point:** [`ensemble-engine/app/main.py`](ensemble-engine/app/main.py)

### HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/decision/{symbol}` | Read latest ensemble decision from Redis `ensemble:{symbol}` — [line 207–213](ensemble-engine/app/main.py#L207) |

### RabbitMQ — Consumes

| Queue | Exchange | Routing keys | Handler |
|---|---|---|---|
| `agent.signals` | `agent.signals` | `technical`, `sentiment`, `macro`, `pattern`, `rl` | `AgentSignalCollector` — waits for ALL 5 signals (timeout: `AGENT_SIGNAL_TIMEOUT_SECONDS` = 5s) |

**File:** [`ensemble-engine/app/main.py`](ensemble-engine/app/main.py) — collector startup at [line 180](ensemble-engine/app/main.py#L180)

### Aggregation Logic

**File:** [`ensemble-engine/app/main.py`](ensemble-engine/app/main.py) — `on_all_signals_received()` at [line 145](ensemble-engine/app/main.py#L145)

```
5 agent signals received
  → weighted_confidence = Σ(agent_confidence × agent_weight)
  → final_action = argmax(weighted votes)
  → agreement_score = fraction of agents agreeing
```

Weights are loaded from PostgreSQL `agent_weights` table (updated by `feedback-service`).

### RabbitMQ — Publishes

| Exchange | Routing Key | Payload | Line |
|---|---|---|---|
| `ensemble.decision` | `decision` | `{symbol, exchange, final_action, weighted_confidence, agent_votes, agreement_score, uncertainty, scores, weights_used, current_price, atr}` | [145–155](ensemble-engine/app/main.py#L145) |

### Redis — Writes

| Key | Value | TTL | Line |
|---|---|---|---|
| `ensemble:{SYMBOL}` | Ensemble decision JSON | 300 s | [134–139](ensemble-engine/app/main.py#L134) |

---

## 7. Risk Engine (Port 8010)

**Language:** Java / Spring Boot  
**Entry point:** [`risk-engine/`](risk-engine/)

### RabbitMQ — Consumes

| Queue | Exchange | Routing Key |
|---|---|---|
| `ensemble.decision` | `ensemble.decision` | `decision` |

### Processing

Applies configurable risk gates:

| Parameter | Env Var | Default |
|---|---|---|
| Minimum signal confidence | `MIN_CONFIDENCE` | `0.60` |
| Max position size | `MAX_POSITION_PCT` | `5%` of portfolio |
| Max risk per trade | `MAX_RISK_PCT` | `2%` |
| Stop-loss multiplier (ATR) | `ATR_STOP_MULT` | `2.0` |
| Take-profit multiplier (ATR) | `ATR_PROFIT_MULT` | `3.0` |

### RabbitMQ — Publishes

| Exchange | Routing Key | Payload |
|---|---|---|
| `risk.validated` | `validated` | `{symbol, decision, risk_metrics, stop_loss, take_profit, position_size, validation_status}` |

---

## 8. Trade Executor (Port 8011)

**Language:** Java / Spring Boot  
**Entry point:** [`trade-executor/`](trade-executor/)  
**Mode:** Controlled by `PAPER_TRADING_MODE=true` env var.

### RabbitMQ — Consumes

| Queue | Exchange | Routing Key |
|---|---|---|
| `risk.validated` | `risk.validated` | `validated` |

### Processing

- **Paper mode** (`PAPER_TRADING_MODE=true`): simulates fill, no real API call
- **Live mode**: calls Groww broker API to place order

### RabbitMQ — Publishes

| Exchange | Routing Key | Payload |
|---|---|---|
| `trade.outcomes` | _(fanout)_ | `{symbol, action, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, market_context, timestamp}` |

**Subscribers of `trade.outcomes`:**
- `trade.outcomes.feedback` → feedback-service
- `trade.outcomes.rl` → rl-agent

---

## 9. Feedback Service (Port 8012)

**Entry point:** [`feedback-service/app/main.py`](feedback-service/app/main.py)

### HTTP Endpoints

| Method | Path | Description | Called by |
|--------|------|-------------|-----------|
| `POST` | `/trades` | Ingest backtest trade records into PostgreSQL | backend/app/api/backtest.py [line 41](backend/app/api/backtest.py#L41) |

### RabbitMQ — Consumes

**Consumer loop:** [`feedback-service/app/main.py`](feedback-service/app/main.py) — `_consumer_loop()` at [line 164](feedback-service/app/main.py#L164)

| Queue | Exchange | Handler operations |
|---|---|---|
| `trade.outcomes.feedback` | `trade.outcomes` | 1. Store trade in PostgreSQL `trade_records` · 2. Store RL experience in `rl_experiences` · 3. Update `agent_weights` · 4. Maybe trigger retrain |

### Retraining Trigger

**File:** [`feedback-service/app/main.py`](feedback-service/app/main.py) — `_maybe_trigger_retrain()` at [line 148](feedback-service/app/main.py#L148)

```python
# Every RETRAIN_THRESHOLD (default 500) trades:
publish(exchange="model.retrain", routing_key="retrain",
        body={"reason": f"retrain_threshold_{N}_reached"})
```

### PostgreSQL — Writes

| Table | Columns |
|---|---|
| `trade_records` | `trade_id, symbol, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, market_context, timestamp_open, timestamp_close, trade_source` |
| `rl_experiences` | `symbol, state, action, reward, next_state, done` |
| `agent_weights` | `agent, weight, updated_at` |

---

## 10. Model Trainer (Port 8013)

**Entry point:** [`model-trainer/`](model-trainer/)

### RabbitMQ — Consumes

| Queue | Exchange | Handler |
|---|---|---|
| `model.retrain` | `model.retrain` | Triggers retraining pipeline |

### What it retrains

| Model | Data source | Schedule |
|---|---|---|
| RL trading policy | PostgreSQL `rl_experiences` (last `TRAIN_DAYS`=365 days) | On-demand via `model.retrain` message, or every `RETRAIN_SCHEDULE_HOURS`=24h |
| Technical / pattern models | PostgreSQL `ohlcv` | Same |

### MLflow

All model artifacts and metrics are stored to MLflow at `http://mlflow:5000`.  
`MLFLOW_TRACKING_URI=http://mlflow:5000`

---

## 11. RabbitMQ Topology

**Setup file:** [`market-data-service/app/services/rabbitmq_setup.py`](market-data-service/app/services/rabbitmq_setup.py)

### Exchanges

| Exchange | Type | Line | Purpose |
|---|---|---|---|
| `market.data` | `fanout` | [11](market-data-service/app/services/rabbitmq_setup.py#L11) | Broadcast raw tick to all agents |
| `agent.signals` | `direct` | [12](market-data-service/app/services/rabbitmq_setup.py#L12) | Route agent signal to ensemble by agent name |
| `ensemble.decision` | `direct` | [13](market-data-service/app/services/rabbitmq_setup.py#L13) | Send ensemble decision to risk-engine |
| `risk.validated` | `direct` | [14](market-data-service/app/services/rabbitmq_setup.py#L14) | Send validated decision to trade-executor |
| `trade.orders` | `direct` | [15](market-data-service/app/services/rabbitmq_setup.py#L15) | Internal order routing |
| `trade.outcomes` | `fanout` | [16](market-data-service/app/services/rabbitmq_setup.py#L16) | Broadcast trade result to feedback + rl-agent |
| `model.retrain` | `direct` | [17](market-data-service/app/services/rabbitmq_setup.py#L17) | Trigger model retraining |
| `notifications` | `fanout` | [18](market-data-service/app/services/rabbitmq_setup.py#L18) | Broadcast system notifications |

### Queue Bindings

| Queue | Exchange | Routing Key | Consumer Service | Line |
|---|---|---|---|---|
| `market.data.technical` | `market.data` | _(fanout)_ | technical-agent | [24](market-data-service/app/services/rabbitmq_setup.py#L24) |
| `market.data.sentiment` | `market.data` | _(fanout)_ | sentiment-agent | [25](market-data-service/app/services/rabbitmq_setup.py#L25) |
| `market.data.macro` | `market.data` | _(fanout)_ | macro-agent | [26](market-data-service/app/services/rabbitmq_setup.py#L26) |
| `market.data.pattern` | `market.data` | _(fanout)_ | pattern-agent | [27](market-data-service/app/services/rabbitmq_setup.py#L27) |
| `market.data.rl` | `market.data` | _(fanout)_ | rl-agent | [28](market-data-service/app/services/rabbitmq_setup.py#L28) |
| `agent.signals` | `agent.signals` | `technical` | ensemble-engine | [30](market-data-service/app/services/rabbitmq_setup.py#L30) |
| `agent.signals` | `agent.signals` | `sentiment` | ensemble-engine | [31](market-data-service/app/services/rabbitmq_setup.py#L31) |
| `agent.signals` | `agent.signals` | `macro` | ensemble-engine | [32](market-data-service/app/services/rabbitmq_setup.py#L32) |
| `agent.signals` | `agent.signals` | `pattern` | ensemble-engine | [33](market-data-service/app/services/rabbitmq_setup.py#L33) |
| `agent.signals` | `agent.signals` | `rl` | ensemble-engine | [34](market-data-service/app/services/rabbitmq_setup.py#L34) |
| `ensemble.decision` | `ensemble.decision` | `decision` | risk-engine, trade-executor | [36](market-data-service/app/services/rabbitmq_setup.py#L36) |
| `risk.validated` | `risk.validated` | `validated` | trade-executor | [37](market-data-service/app/services/rabbitmq_setup.py#L37) |
| `trade.orders` | `trade.orders` | `order` | trade-executor | [38](market-data-service/app/services/rabbitmq_setup.py#L38) |
| `trade.outcomes.feedback` | `trade.outcomes` | _(fanout)_ | feedback-service | [40](market-data-service/app/services/rabbitmq_setup.py#L40) |
| `trade.outcomes.rl` | `trade.outcomes` | _(fanout)_ | rl-agent | [41](market-data-service/app/services/rabbitmq_setup.py#L41) |
| `model.retrain` | `model.retrain` | `retrain` | model-trainer | [43](market-data-service/app/services/rabbitmq_setup.py#L43) |
| `notifications.all` | `notifications` | _(fanout)_ | dashboards / UI | [44](market-data-service/app/services/rabbitmq_setup.py#L44) |

---

## 12. Redis Key Reference

| Key Pattern | Writer | Reader(s) | TTL | Purpose |
|---|---|---|---|---|
| `tick:{SYMBOL}` | market-data-service | backend/stocks.py, backend/predictions.py | 120 s | Latest price tick |
| `candle:{SYMBOL}:{interval}` | market-data-service | backend/stocks.py | 300 s | Recent OHLCV candles |
| `ensemble:{SYMBOL}` | ensemble-engine [line 134](ensemble-engine/app/main.py#L134) | backend/predictions.py, ensemble-engine/main.py | 300 s | Latest ensemble decision |
| `otp:{phone_or_email}` | backend/auth.py | backend/auth.py | 300 s | OTP verification |
| `session:{token}` | backend/auth.py | backend/middleware | varies | Session / JWT blacklist |
| `macro:{key}` | macro-agent | macro-agent | 3600 s | Cached macro indicators |

---

## 13. Database Schema Overview

### PostgreSQL (TimescaleDB)

| Table | Key Columns | Written by | Read by |
|---|---|---|---|
| `users` | `id, email, phone, password_hash, broker, broker_api_key, broker_api_secret, is_verified` | backend/auth.py | backend/auth.py |
| `ohlcv` | `symbol, exchange, interval, time, open, high, low, close, volume` | market-data-service | backend/stocks.py, technical-agent, pattern-agent, rl-agent, model-trainer |
| `trade_records` | `trade_id, symbol, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, market_context, timestamp_open, timestamp_close, trade_source` | feedback-service | backend/predictions.py, model-trainer |
| `rl_experiences` | `symbol, state, action, reward, next_state, done` | feedback-service | model-trainer |
| `agent_weights` | `agent, weight, updated_at` | feedback-service | ensemble-engine |

### MongoDB

| Collection | Written by | Read by | Purpose |
|---|---|---|---|
| `news` | market-data-service | sentiment-agent | Raw news articles per symbol |
| `sentiment_scores` | sentiment-agent | backend/stocks.py | Sentiment analysis results |
| `alerts` | backend/portfolio.py | backend/portfolio.py | User price/pattern alerts |

---

## 14. Inter-Service HTTP Calls

| Caller | Target | Method | URL | File | Line | Pattern |
|---|---|---|---|---|---|---|
| `backend` | `feedback-service:8012` | `POST` | `/trades` | [backend/app/api/backtest.py](backend/app/api/backtest.py) | [41](backend/app/api/backtest.py#L41) | Fire-and-forget (`asyncio.create_task`) |
| `backend` | `mlflow:5000` | `GET/POST` | `/api/mlflow/*` (proxied) | [backend/app/api/mlflow_proxy.py](backend/app/api/mlflow_proxy.py) | — | Transparent proxy |
| `backend` | `Groww API` | `GET/POST` | External | [backend/app/utils/groww_client.py](backend/app/utils/groww_client.py) | — | Auth-gated REST |
| `market-data-service` | `Groww API` | `GET` | External | [market-data-service/app/services/](market-data-service/app/services/) | — | Price tick polling |
| `market-data-service` | `Yahoo Finance` | `GET` | External | same | — | Fallback tick source |
| `market-data-service` | `NewsAPI` | `GET` | External | same | — | News ingestion |
| `trade-executor` | `Groww API` | `POST` | External | [trade-executor/](trade-executor/) | — | Live order placement |

---

## 15. Dependency Matrix

| Service | Publishes to (RabbitMQ) | Consumes from (RabbitMQ) | PostgreSQL | Redis | MongoDB | HTTP Out |
|---|---|---|---|---|---|---|
| **market-data-service** | `market.data`, `notifications` | — | Write `ohlcv` | Write `tick:*`, `candle:*` | Write `news` | Groww, Yahoo, NewsAPI |
| **technical-agent** | `agent.signals` (technical) | `market.data.technical` | Read `ohlcv` | — | — | — |
| **sentiment-agent** | `agent.signals` (sentiment) | `market.data.sentiment` | — | — | Read `news` | — |
| **macro-agent** | `agent.signals` (macro) | `market.data.macro` | — | Read/Write `macro:*` | — | External macro APIs |
| **pattern-agent** | `agent.signals` (pattern) | `market.data.pattern` | Read `ohlcv` | — | — | — |
| **rl-agent** | `agent.signals` (rl) | `market.data.rl`, `trade.outcomes.rl` | Read `ohlcv`, `rl_experiences` | Read | — | — |
| **ensemble-engine** | `ensemble.decision` | `agent.signals` (×5) | Read `agent_weights` | Write `ensemble:*` | — | — |
| **risk-engine** | `risk.validated` | `ensemble.decision` | — | — | — | — |
| **trade-executor** | `trade.outcomes` | `risk.validated` | — | — | — | Groww API |
| **feedback-service** | `model.retrain` | `trade.outcomes.feedback` | Write `trade_records`, `rl_experiences`, `agent_weights` | — | — | — |
| **model-trainer** | — | `model.retrain` | Read all | — | — | MLflow |
| **backend** | — | — | Read/Write `users` | Read `tick:*`, `ensemble:*` | Read/Write `alerts`, `sentiment_scores` | Groww API, feedback-service |

---

*Generated from source — update this file when adding new endpoints or message types.*
