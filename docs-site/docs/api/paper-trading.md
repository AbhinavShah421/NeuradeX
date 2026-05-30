---
id: paper-trading
title: Paper Trading
sidebar_position: 9
---

# Paper Trading — `/api/paper-trading`

**File:** [`backend/app/api/paper_trading.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/paper-trading/status` | [498](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py#L498) | Session state — balance, P&L, open positions |
| `POST` | `/api/paper-trading/start` | [523](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py#L523) | Start new session with initial capital |
| `POST` | `/api/paper-trading/step` | [646](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py#L646) | Advance session one tick via agent decision |
| `GET` | `/api/paper-trading/tick/{symbol}` | [780](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py#L780) | Next simulated price tick |
| `POST` | `/api/paper-trading/place-order` | [968](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/paper_trading.py#L968) | Simulate order (no real money) |

No real broker calls are made — all fills are simulated against live price ticks.
