---
id: backtest
title: Backtest
sidebar_position: 8
---

# Backtest — `/api/backtest`

**File:** [`backend/app/api/backtest.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/backtest/strategies` | [456](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L456) | List available strategy configs |
| `POST` | `/api/backtest/run` | [461](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L461) | Run a full historical backtest |
| `GET` | `/api/backtest/live-signal/{symbol}` | [546](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L546) | Current live signal for a symbol |
| `POST` | `/api/backtest/day-autopilot` | [871](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L871) | Full-day autopilot simulation |
| `GET` | `/api/backtest/intraday-candles/{symbol}` | [1104](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L1104) | 1-min intraday candles |
| `POST` | `/api/backtest/agent-step` | [1177](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L1177) | Advance backtest by one agent decision step |
| `POST` | `/api/backtest/progressive/start` | [1392](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L1392) | Start a progressive (streaming) session |
| `POST` | `/api/backtest/progressive/step` | [1512](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/backtest.py#L1512) | Advance one candle at a time |

## Inter-Service Call

After `POST /backtest/run` completes, trade records are asynchronously sent to feedback-service:

```python title="backend/app/api/backtest.py:41"
FEEDBACK_SERVICE_URL = "http://feedback-service:8012"

asyncio.create_task(
    httpx.post(f"{FEEDBACK_SERVICE_URL}/trades", json=trade_records)
)
```
