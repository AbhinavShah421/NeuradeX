---
id: ensemble-engine
title: Ensemble Engine
sidebar_position: 3
---

# Ensemble Engine — Port 8007

**Entry point:** [`ensemble-engine/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py)

Aggregates signals from all 5 agents into a single weighted decision.

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/decision/{symbol}` | Read latest decision from Redis `ensemble:{symbol}` |

## RabbitMQ — Consumes

**Collector startup:** [line 180](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py#L180)

| Queue | Routing Keys | Timeout |
|---|---|---|
| `agent.signals` | `technical`, `sentiment`, `macro`, `pattern`, `rl` | `AGENT_SIGNAL_TIMEOUT_SECONDS` = 5s |

## Aggregation Logic

**Function:** `on_all_signals_received()` at [line 145](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py#L145)

```python
weighted_confidence = Σ(agent_confidence × agent_weight)
final_action        = argmax(weighted votes across BUY/SELL/HOLD)
agreement_score     = fraction of agents agreeing with final_action
```

Weights are loaded from PostgreSQL `agent_weights` table (updated by feedback-service after every trade).

## RabbitMQ — Publishes

| Exchange | Routing Key | Payload | Line |
|---|---|---|---|
| `ensemble.decision` | `decision` | `{symbol, exchange, final_action, weighted_confidence, agent_votes, agreement_score, uncertainty, scores, weights_used, current_price, atr}` | [145](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py#L145) |

## Redis — Writes

| Key | TTL | Line |
|---|---|---|
| `ensemble:{SYMBOL}` | 300 s | [134](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py#L134) |

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `AGENT_SIGNAL_TIMEOUT_SECONDS` | `5` | How long to wait for all 5 agents |
| `MIN_CONFIDENCE_TO_TRADE` | `0.60` | Minimum weighted confidence to emit a decision |
