---
id: data-flow
title: End-to-End Data Flow
sidebar_position: 1
---

# End-to-End Data Flow

A price tick from the broker API travels through 7 stages before becoming an executed trade.

```
  [Groww API / Yahoo Finance / NewsAPI]
              │
              ▼
  ┌──────────────────────┐   publish: market.data (fanout)
  │  market-data-service  │ ─────────────────────────────────────┐
  │       :8001           │                                      │
  └──────────────────────┘                                      │
        │ Redis: tick:{symbol}                                   │
        │                            ┌───────────┬───────────┬──┴────────┬──────────────┐
        │                            │ technical │ sentiment │   macro   │   pattern    │  rl-agent
        │                            │  :8002    │   :8003   │   :8004   │   :8005      │  :8006
        │                            └─────┬─────┴─────┬─────┴─────┬─────┴──────┬───────┴────┬──────┘
        │                                  │           │           │            │             │
        │                                  └───────────┴───────────┴────────────┴─────────────┘
        │                                            publish: agent.signals (direct)
        │                                                        │
        │                                                        ▼
        │                                         ┌──────────────────────┐
        │                                         │   ensemble-engine    │
        │                                         │        :8007         │
        │                                         │  waits for 5 agents  │
        │                                         └──────────┬───────────┘
        │                                  Redis: ensemble:{symbol} (TTL 300s)
        │                                                    │
        │                                     ┌──────────────┴──────────────┐
        │                                     ▼                             ▼
        │                          ┌────────────────────┐     ┌────────────────────┐
        │                          │    risk-engine     │     │   trade-executor   │
        │                          │       :8010        │     │       :8011        │
        │                          │   (Java/Spring)    │     │   (Java/Spring)    │
        │                          └─────────┬──────────┘     └────────────────────┘
        │                                    │ publish: risk.validated
        │                                    ▼
        │                          ┌────────────────────┐
        │                          │   trade-executor   │ ──→  Groww API (live)
        │                          │       :8011        │      or paper mode
        │                          └─────────┬──────────┘
        │                                    │ publish: trade.outcomes (fanout)
        │                          ┌─────────┴──────────┐
        │                          ▼                     ▼
        │               ┌──────────────────┐  ┌──────────────────┐
        │               │ feedback-service  │  │    rl-agent      │
        │               │      :8012        │  │     :8006        │
        │               │ updates weights   │  │  updates policy  │
        │               └────────┬──────────┘  └──────────────────┘
        │                        │ publish: model.retrain (every N trades)
        │                        ▼
        │               ┌──────────────────┐
        │               │  model-trainer   │ ──→  MLflow :5000
        │               │     :8013        │
        │               └──────────────────┘
        │
        ▼
  [backend :8000] ←→ Frontend :3000  (REST + WebSocket)
```

---

## The self-improving loop (scanner → autopilot → learning)

Running alongside the trade pipeline above is the loop that makes the system
**smarter every day**:

```
  stock-scanner :8014  ──▶  AI watchlist (Redis: ai_engine:watchlist)
   (pre-open + intraday)            │
        ▲                           ▼
        │                  autopilot (if ON, market open)
        │                  opens a paper session per watchlist stock
        │                           │
        │                  12-agent ensemble decides on real data
        │                           │
        │                  closed trades → train weights + RL + memory
        │                           │
        │                  ┌────────┴─────────┐
        │                  ▼                  ▼
        │        System Learning Curve   Orders / trade_records
        │
        └── post-market signal score grades the morning picks ──┐
                  (scanner /evaluate → /api/ai-engine/scan-feedback)
                  accuracy ──▶ calibration ──▶ sharper next scan ─┘
```

See [Stock Scanner](../microservices/stock-scanner.md),
[Watchlist & Autopilot](../ai-engine/watchlist-autopilot.md), and
[Learning & Pattern Memory](../ai-engine/learning-loop.md).

---

## Stage-by-Stage Breakdown

### Stage 1 — Market Data Ingestion
- **Service:** `market-data-service:8001`
- Polls Groww API / Yahoo Finance every 60 seconds
- Writes `tick:{SYMBOL}` to Redis (TTL 120s)
- Publishes raw OHLCV tick to `market.data` exchange (fanout → all 5 agents)
- Simultaneously polls NewsAPI every 300s; stores articles in MongoDB `news`

### Stage 2 — Parallel Agent Analysis
Five agents consume from their dedicated queues simultaneously:

| Agent | Queue | Signal type |
|---|---|---|
| technical-agent | `market.data.technical` | RSI, MACD, Bollinger |
| sentiment-agent | `market.data.sentiment` | FinBERT score on recent news |
| macro-agent | `market.data.macro` | FII/DII flows, GDP, inflation |
| pattern-agent | `market.data.pattern` | Candlestick pattern recognition |
| rl-agent | `market.data.rl` | Learned policy from past trades |

Each publishes `{signal, confidence, reasoning}` to `agent.signals` exchange.

### Stage 3 — Ensemble Aggregation
- **Service:** `ensemble-engine:8007`
- Waits up to 5 seconds for all 5 agents
- Computes `weighted_confidence = Σ(agent_confidence × agent_weight)`
- Weights are stored in PostgreSQL `agent_weights` (updated by feedback-service after each trade outcome)
- Publishes final `{final_action, weighted_confidence, agreement_score}` to `ensemble.decision`
- Caches result in Redis `ensemble:{SYMBOL}` for 300s (served by backend `/api/predictions/{symbol}`)

### Stage 4 — Risk Validation
- **Service:** `risk-engine:8010` (Java/Spring Boot)
- Applies gates: min confidence 0.60, max position 5%, max risk 2%, ATR-based stop/take-profit
- Publishes `{validation_status, stop_loss, take_profit, position_size}` to `risk.validated`

### Stage 5 — Trade Execution
- **Service:** `trade-executor:8011` (Java/Spring Boot)
- Paper mode (`PAPER_TRADING_MODE=true`): simulates fill
- Live mode: calls Groww broker API
- Publishes trade outcome to `trade.outcomes` (fanout)

### Stage 6 — Feedback Loop
- **feedback-service:8012** stores trade result in PostgreSQL, updates `agent_weights`
- **rl-agent:8006** receives outcome to update its policy
- After every 500 trades, publishes to `model.retrain`

### Stage 7 — Model Retraining
- **model-trainer:8013** retrains RL policy + technical/pattern models
- Artifacts stored in MLflow at `http://mlflow:5000`
