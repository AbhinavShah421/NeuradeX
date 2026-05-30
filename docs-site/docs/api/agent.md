---
id: agent
title: AI Agent
sidebar_position: 7
---

# AI Agent — `/api/agent`

**File:** [`backend/app/api/agent.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/agent.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/agent/stocks` | [357](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/agent.py#L357) | Stocks currently under agent analysis |
| `GET` | `/api/agent/models` | [385](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/agent.py#L385) | Status and accuracy of each agent model |
| `POST` | `/api/agent/analyze/{symbol}` | [407](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/agent.py#L407) | Synchronous inline analysis (all 5 agents) |

`POST /agent/analyze/{symbol}` runs all agent classes inline (not via RabbitMQ) — useful for on-demand analysis from the frontend.

Agent classes used:
- [`backend/app/agents/technical.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/technical.py)
- [`backend/app/agents/sentiment.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/sentiment.py)
- [`backend/app/agents/pattern.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/pattern.py)
- [`backend/app/agents/momentum.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/momentum.py)
- [`backend/app/agents/rl_agent.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/rl_agent.py)
- [`backend/app/agents/ensemble.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/ensemble.py)
