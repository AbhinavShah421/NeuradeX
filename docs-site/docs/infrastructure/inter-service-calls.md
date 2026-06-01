---
id: inter-service-calls
title: Inter-Service HTTP Calls
sidebar_position: 4
---

# Inter-Service HTTP Calls

Direct HTTP calls between services (as opposed to RabbitMQ messaging).

| Caller | Target | Method | URL | File | Line | Pattern |
|---|---|---|---|---|---|---|
| `backend` | `feedback-service:8012` | `POST` | `/trades` | [backtest.py](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py) | [41](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L41) | Fire-and-forget (`asyncio.create_task`) |
| `backend` | `stock-scanner:8014` | `POST` | `/scan` | [ai_engine.py](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/ai_engine.py) | — | Manual **Rescan** → trigger a full sweep |
| `stock-scanner` | `backend:8000` | `POST` | `/api/ai-engine/scan-feedback` | [scanner.py](https://github.com/AbhinavShah421/NeuradeX/blob/main/stock-scanner/app/scanner.py) | — | Post-market signal-score grade → persisted to `scan_evaluations` |
| `backend` | `mlflow:5000` | `GET/POST` | `/api/mlflow/*` | [mlflow_proxy.py](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/mlflow_proxy.py) | — | Transparent proxy |
| `backend` | Groww API | `GET/POST` | External | [groww_client.py](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/utils/groww_client.py) | — | Auth-gated REST |
| `market-data-service` | Groww API | `GET` | External | [market-data-service/app/services/](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/) | — | Price tick polling (60s) |
| `market-data-service` | Yahoo Finance | `GET` | External | same | — | Fallback tick source |
| `market-data-service` | NewsAPI | `GET` | External | same | — | News ingestion (300s) |
| `trade-executor` | Groww API | `POST` | External | [trade-executor/](https://github.com/AbhinavShah421/NeuradeX/blob/main/trade-executor/) | — | Live order placement |

## Backtest → Feedback Fire-and-Forget

```python title="backend/app/api/backtest.py:27,41"
FEEDBACK_SERVICE_URL = "http://feedback-service:8012"

# After backtest completes — does not block the response
asyncio.create_task(
    httpx.post(f"{FEEDBACK_SERVICE_URL}/trades", json=trade_records)
)
```

## Public reverse proxy (`nginx`)

All public traffic (via ngrok) enters through the **`nginx`** service
([`config/nginx.conf`](https://github.com/AbhinavShah421/NeuradeX/blob/main/config/nginx.conf)),
which routes:

| Public path | → Service |
|---|---|
| `/api/*`, `/socket.io/*`, `/health` | `backend:8000` |
| `/neuradex/backend/*` | `backend:8000` (backend runs with `--root-path /neuradex/backend`) |
| `/neuradex/dev/*` | `docs:3001` |
| `/neuradex/dev/logs` | `kibana:5601` |
| `/` and `/neuradex/*` | `frontend:3000` |

:::note Dynamic upstream resolution
nginx uses Docker's embedded DNS (`resolver 127.0.0.11`) with the upstream host
in a variable in each `proxy_pass`, so it **re-resolves at request time**.
Recreating a container (which changes its IP) no longer leaves nginx proxying a
dead IP — i.e. no more app-wide `502`s after a redeploy.
:::
