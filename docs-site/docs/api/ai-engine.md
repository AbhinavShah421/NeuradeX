---
id: ai-engine
title: AI Engine
sidebar_position: 10
---

# AI Engine — `/api/ai-engine`

**File:** [`backend/app/api/ai_engine.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/ai_engine.py)

The AI Engine API drives the **12-agent ensemble**, the **continuous learning
loop**, the **AI watchlist + autopilot**, and **Pattern Memory**.

### Ensemble & learning

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ai-engine/analyze` | Run the 12-agent ensemble on a candle window; stores a prediction |
| `POST` | `/api/ai-engine/outcome` | Record a trade outcome → trains agent weights + RL Q-table + memory |
| `GET` | `/api/ai-engine/performance` | Per-agent weight + accuracy |
| `GET` | `/api/ai-engine/learning-summary` | Totals, overall accuracy, per-agent stats, 24h activity, memory size |
| `GET` | `/api/ai-engine/learning-curve` | Cumulative win-rate over the ordered trade history (drives the dashboard curve) |

### AI watchlist, autopilot & signal score

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/ai-engine/watchlist` | Live ranked AI watchlist (`items` = intraday, `delivery` = multi-week swing picks), read from Redis, produced by the [stock-scanner](../microservices/stock-scanner.md) |
| `POST` | `/api/ai-engine/watchlist/scan` | Proxy a manual full-market sweep to the scanner service |
| `GET` | `/api/ai-engine/scan-status` | **Centralized scan status** — `{scanning, scanned, universe, candidates, last_scan}`. Single source of truth so a rescan started on one page disables rescan on all pages |
| `GET` | `/api/ai-engine/ranked?limit=N` | Full **ranked board** of AI-scanned stocks (top N, default 100) with per-stock evidence + news sentiment — backs the Predictions rankings page |
| `GET` | `/api/ai-engine/autopilot` | Combined autopilot status — `paper` + `backtest` (proxied from autopilot-service) |
| `POST` | `/api/ai-engine/autopilot` | Enable / disable a mode (`{ "mode": "paper"\|"backtest", "enabled": true }`) |
| `POST` | `/api/ai-engine/autopilot/reset-cursor` | Reset the backtest next trade date to the last trading day before today (stops the in-flight queue, keeps training history) |
| `GET` | `/api/ai-engine/llm-status` | Active LLM provider (Anthropic vs Ollama), model, and a live probe |
| `GET` | `/api/ai-engine/scan-evaluation` | Latest post-market signal-score grade + per-day accuracy trend |
| `POST` | `/api/ai-engine/scan-feedback` | (Internal) the scanner pushes its post-market grade here → persisted to `scan_evaluations` |

### AI loss-learning (why trades lose + lessons)

The system already learns *quantitatively* (per-agent weight updates, scanner
calibration, and the ensemble's pattern-memory veto). These endpoints add the
*explanatory* layer: an LLM post-mortem on each losing trade, aggregated into
reusable **lessons** that are injected into the AI's decision prompts. See
[Learning & Pattern Memory](../ai-engine/learning-loop.md#ai-loss-learning).

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ai-engine/loss-learning/run` | Analyse recent losing trades (from feedback-service) that lack a post-mortem; the LLM explains each (`root_cause`, `failure_mode`, `factors`, `lesson`, `avoid_when`), stores it in `trade_postmortems`, and refreshes the aggregated lessons (rule-based fallback if the LLM is unavailable) |
| `GET` | `/api/ai-engine/loss-learning/postmortems?limit=N` | Recent per-trade loss post-mortems |
| `GET` | `/api/ai-engine/loss-learning/lessons` | Aggregated lessons — recurring failure modes ranked by occurrences + avg loss; cached to `ai_engine:active_lessons` and prepended to the AI analysis prompt |

### Pattern Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/ai-engine/memory/stats` | Memory size + win-rate by source/action |
| `POST` | `/api/ai-engine/memory/query` | What memory recalls for a candle window |
| `POST` | `/api/ai-engine/memory/seed` | Bulk-seed memory from historical replays |
| `POST` | `/api/ai-engine/memory/sweep` | Trigger the nightly memory rebuild |
| `GET/POST` | `/api/mlflow/*` | Transparent proxy to MLflow at `http://mlflow:5000` |

See [Watchlist & Autopilot](../ai-engine/watchlist-autopilot.md) and
[Learning & Pattern Memory](../ai-engine/learning-loop.md) for the flows behind
these endpoints.

**MLflow proxy file:** [`backend/app/api/mlflow_proxy.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/mlflow_proxy.py)

## System Routes

**File:** [`backend/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/main.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/` | [100](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/main.py#L100) | Service name + version |
| `GET` | `/health` | [105](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/main.py#L105) | DB + Redis connectivity check |

## WebSocket

**File:** [`backend/app/websocket/socket_manager.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/websocket/socket_manager.py)

Socket.IO server mounted on `app_sio`. Frontend connects to `VITE_SOCKET_URL=http://localhost:8000`.

| Event | Direction | Description |
|---|---|---|
| `tick_update` | server → client | Real-time price tick |
| `prediction_update` | server → client | New ensemble decision |
| `alert_triggered` | server → client | User alert fired |
