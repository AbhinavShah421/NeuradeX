---
id: risk
title: Risk Analytics
sidebar_position: 6
---

# Risk Analytics — `/api/risk`

**File:** [`backend/app/api/risk.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/risk/var` | [205](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py#L205) | Value-at-Risk (95% and 99%) for portfolio |
| `GET` | `/api/risk/stress-test` | [269](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py#L269) | Historical shock scenario stress tests |
| `GET` | `/api/risk/factors` | [360](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py#L360) | Factor exposure (beta, size, value, momentum) |
| `GET` | `/api/risk/optimization` | [507](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py#L507) | Mean-variance optimal portfolio weights |
| `GET` | `/api/risk/optimization/analyze` | [619](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/risk.py#L619) | LLM explanation of current vs optimal gap |

**Data source:** PostgreSQL `ohlcv` table for historical prices + Groww API for current holdings.
