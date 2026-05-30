---
id: ai-engine
title: AI Engine
sidebar_position: 10
---

# AI Engine — `/api/ai-engine`

**File:** [`backend/app/api/ai_engine.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/ai_engine.py)

LLM-backed conversational analysis and MLflow proxy.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ai-engine/analyze` | Analyze candles with LLM — returns trade recommendation + reasoning |
| `POST` | `/api/ai-engine/outcome` | Record trade outcome for a previous prediction |
| `GET` | `/api/ai-engine/performance` | LLM prediction accuracy stats |
| `GET` | `/api/ai-engine/history` | Historical LLM predictions |
| `GET` | `/api/ai-engine/weights` | Current ensemble agent weights |
| `GET/POST` | `/api/mlflow/*` | Transparent proxy to MLflow at `http://mlflow:5000` |

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
