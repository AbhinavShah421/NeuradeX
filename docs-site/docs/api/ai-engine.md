---
id: ai-engine
title: AI Engine
sidebar_position: 10
---

# AI Engine — `/api/ai-engine`

**File:** [`backend/app/api/ai_engine.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/ai_engine.py)

The AI Engine API drives the **7-agent ensemble**, the **continuous learning
loop**, the **AI watchlist + autopilot**, and **Pattern Memory**.

### Ensemble & learning

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ai-engine/analyze` | Run the 7-agent ensemble on a candle window; stores a prediction |
| `POST` | `/api/ai-engine/outcome` | Record a trade outcome → trains agent weights + RL Q-table + memory |
| `GET` | `/api/ai-engine/performance` | Per-agent weight + accuracy |
| `GET` | `/api/ai-engine/learning-summary` | Totals, overall accuracy, per-agent stats, 24h activity, memory size |
| `GET` | `/api/ai-engine/learning-curve` | Cumulative win-rate over the ordered trade history (drives the dashboard curve) |

### AI watchlist, autopilot & signal score

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/ai-engine/watchlist` | Live ranked AI watchlist (read from Redis, produced by the [stock-scanner](../microservices/stock-scanner.md)) |
| `POST` | `/api/ai-engine/watchlist/scan` | Proxy a manual full sweep to the scanner service |
| `GET` | `/api/ai-engine/autopilot` | Autopilot status (enabled, market, watchlist size, started today, running sessions) |
| `POST` | `/api/ai-engine/autopilot` | Enable / disable autopilot (`{ "enabled": true }`) |
| `GET` | `/api/ai-engine/scan-evaluation` | Latest post-market signal-score grade + per-day accuracy trend |
| `POST` | `/api/ai-engine/scan-feedback` | (Internal) the scanner pushes its post-market grade here → persisted to `scan_evaluations` |

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
