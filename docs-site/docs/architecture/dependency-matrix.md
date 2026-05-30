---
id: dependency-matrix
title: Dependency Matrix
sidebar_position: 2
---

# Service Dependency Matrix

| Service | Publishes (RabbitMQ) | Consumes (RabbitMQ) | PostgreSQL | Redis | MongoDB | HTTP Out |
|---|---|---|---|---|---|---|
| **market-data-service** | `market.data`, `notifications` | — | Write `ohlcv` | Write `tick:*`, `candle:*` | Write `news` | Groww, Yahoo, NewsAPI |
| **technical-agent** | `agent.signals` (technical) | `market.data.technical` | Read `ohlcv` | — | — | — |
| **sentiment-agent** | `agent.signals` (sentiment) | `market.data.sentiment` | — | — | Read `news` | — |
| **macro-agent** | `agent.signals` (macro) | `market.data.macro` | — | Read/Write `macro:*` | — | External macro APIs |
| **pattern-agent** | `agent.signals` (pattern) | `market.data.pattern` | Read `ohlcv` | — | — | — |
| **rl-agent** | `agent.signals` (rl) | `market.data.rl`, `trade.outcomes.rl` | Read `ohlcv`, `rl_experiences` | Read | — | — |
| **ensemble-engine** | `ensemble.decision` | `agent.signals` (×5) | Read `agent_weights` | Write `ensemble:*` | — | — |
| **risk-engine** | `risk.validated` | `ensemble.decision` | — | — | — | — |
| **trade-executor** | `trade.outcomes` | `risk.validated` | — | — | — | Groww API |
| **feedback-service** | `model.retrain` | `trade.outcomes.feedback` | Write `trade_records`, `rl_experiences`, `agent_weights` | — | — | — |
| **model-trainer** | — | `model.retrain` | Read all | — | — | MLflow |
| **backend** | — | — | Read/Write `users` | Read `tick:*`, `ensemble:*` | Read/Write `alerts`, `sentiment_scores` | Groww API, feedback-service |

**Legend:** Write = primary writer · Read = consumer · R/W = both
