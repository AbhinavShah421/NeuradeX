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
