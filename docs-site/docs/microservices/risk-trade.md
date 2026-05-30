---
id: risk-trade
title: Risk Engine & Trade Executor
sidebar_position: 4
---

# Risk Engine — Port 8010

**Language:** Java / Spring Boot  
**Entry point:** [`risk-engine/`](https://github.com/AbhinavShah421/NeuradeX/blob/main/risk-engine/)

## RabbitMQ — Consumes

| Queue | Exchange | Routing Key |
|---|---|---|
| `ensemble.decision` | `ensemble.decision` | `decision` |

## Risk Gates

| Parameter | Env Var | Default |
|---|---|---|
| Minimum confidence | `MIN_CONFIDENCE` | `0.60` |
| Max position size | `MAX_POSITION_PCT` | `5%` of portfolio |
| Max risk per trade | `MAX_RISK_PCT` | `2%` |
| Stop-loss (ATR multiplier) | `ATR_STOP_MULT` | `2.0` |
| Take-profit (ATR multiplier) | `ATR_PROFIT_MULT` | `3.0` |

## RabbitMQ — Publishes

| Exchange | Routing Key | Payload |
|---|---|---|
| `risk.validated` | `validated` | `{symbol, decision, risk_metrics, stop_loss, take_profit, position_size, validation_status}` |

---

# Trade Executor — Port 8011

**Language:** Java / Spring Boot  
**Entry point:** [`trade-executor/`](https://github.com/AbhinavShah421/NeuradeX/blob/main/trade-executor/)

## Modes

| `PAPER_TRADING_MODE` | Behaviour |
|---|---|
| `true` | Simulates fill, no real API call |
| `false` | Calls Groww broker API |

## RabbitMQ — Consumes

| Queue | Exchange |
|---|---|
| `risk.validated` | `risk.validated` |

## RabbitMQ — Publishes

| Exchange | Type | Payload |
|---|---|---|
| `trade.outcomes` | fanout | `{symbol, action, entry_price, exit_price, pnl_pct, pnl_abs, outcome, agent_signals, market_context, timestamp}` |

**Subscribers of `trade.outcomes`:**
- `trade.outcomes.feedback` → feedback-service
- `trade.outcomes.rl` → rl-agent
