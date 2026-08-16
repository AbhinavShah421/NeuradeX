# NeuradeX — Service Architecture & Data Flow

## System Overview

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                   NEURADEX PLATFORM                         │
                        │                                                             │
  External Sources      │   Infrastructure        Intelligence Layer      Execution   │
  ────────────────      │   ─────────────          ─────────────────      ─────────   │
  Groww API (LTP)  ──►  │                                                             │
  Yahoo Finance    ──►  │  market-data-service ──► agents (×5) ──► ensemble ──► risk  │
  NewsAPI          ──►  │      :8001                                  :8007      :8010│
  RBI / FRED       ──►  │                                                     │       │
                        │                                               trade-executor│
                        │                                                   :8011     │
                        └─────────────────────────────────────────────────────────────┘
```

---

## 1. Full End-to-End Data Flow

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 1 — MARKET DATA INGESTION                           market-data-service :8001      │
│                                                                                          │
│  Groww API (LTP)  ──┐                                                                    │
│  Yahoo Finance    ──┼──► ingestion_loop.py ──► ① Write tick   → Redis  "tick:{symbol}"   │
│  NewsAPI          ──┘          │               ② Write OHLCV  → TimescaleDB `ohlcv`      │
│                                │               ③ Publish tick → RabbitMQ                 │
│                                │                  exchange: market.data (fanout)         │
│                                └──► news_loop.py ──► MongoDB `news_articles`             │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                    market.data (fanout) binds to 5 queues
                                         │
              ┌──────────┬───────────────┼──────────────┬────────────┐
              ▼          ▼               ▼              ▼            ▼
   market.data.    market.data.    market.data.   market.data.  market.data.
   technical       sentiment       macro          pattern       rl

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 2 — AGENT INFERENCE  (all 5 run in parallel)                                      │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐    │
│  │ technical-agent :8002                                                           │    │
│  │  consume market.data.technical                                                  │    │
│  │  → fetch OHLCV from TimescaleDB                                                 │    │
│  │  → compute 15 indicators (EMA,RSI,MACD,BB,ATR,VWAP,Stoch,Williams)              │    │
│  │  → load XGBoost model from MLflow ("technical-xgboost")                         │    │
│  │  → blend 60% ML + 40% rule-based score → BUY / SELL / HOLD + confidence         │    │
│  │  → publish agent.signals  routing_key="technical"                               │    │
│  └─────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐    │
│  │ sentiment-agent :8003                                                           │    │
│  │  consume market.data.sentiment                                                  │    │
│  │  → query MongoDB news_articles for symbol                                       │    │
│  │  → score each article with FinBERT (ProsusAI/finbert)                           │    │
│  │  → aggregate: Reuters×1.0, ET×0.8, Reddit×0.4                                   │    │
│  │  → net_sentiment → BUY / SELL / HOLD + confidence                               │    │
│  │  → publish agent.signals  routing_key="sentiment"                               │    │
│  └─────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐    │
│  │ macro-agent :8004                                                               │    │
│  │  consume market.data.macro                                                      │    │
│  │  → fetch VIX India, USD/INR, crude oil, G-sec yield (live)                      │    │
│  │  → classify market regime: RISK_ON / RISK_OFF / NEUTRAL / STAGFLATION           │    │
│  │  → regime + macro conditions → BUY / SELL / HOLD + confidence                   │    │
│  │  → publish agent.signals  routing_key="macro"                                   │    │
│  └─────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐    │
│  │ pattern-agent :8005                                                             │    │
│  │  consume market.data.pattern                                                    │    │
│  │  → fetch OHLCV from TimescaleDB                                                 │    │
│  │  → detect 8 candlestick patterns (doji, hammer, engulfing, marubozu, stars…)    │    │
│  │  → classify regime with hmmlearn GaussianHMM(3): BULL / BEAR / SIDEWAYS         │    │
│  │  → combined signal → BUY / SELL / HOLD + confidence                             │    │
│  │  → publish agent.signals  routing_key="pattern"                                 │    │
│  └─────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐    │
│  │ rl-agent :8006                                                                  │    │
│  │  consume market.data.rl                                                         │    │
│  │  → fetch OHLCV from TimescaleDB                                                 │    │
│  │  → build observation: [rsi, macd, vol, momentum, ema_dist, position, pnl, …]    │    │
│  │  → load PPO policy from MLflow ("rl-trading-policy")                            │    │
│  │  → action: 0=HOLD, 1=BUY, 2=SELL                                                │    │
│  │  → publish agent.signals  routing_key="rl"                                      │    │
│  └─────────────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                          agent.signals (direct exchange)
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 3 — ENSEMBLE DECISION                               ensemble-engine :8007         │
│                                                                                         │
│  AgentSignalCollector waits up to 5s for all 5 agents per symbol                        │
│                                                                                         │
│  Weighted voting:                                                                       │
│    technical × 0.30  ──┐                                                                │
│    pattern   × 0.20  ──┤                                                                │
│    sentiment × 0.20  ──┼──► weighted_score per action ──► final_action                  │
│    rl        × 0.15  ──┤      if agreement < 0.5 → confidence -20%                      │
│    macro     × 0.15  ──┘      if all disagree   → force HOLD                            │
│                                                                                         │
│  Weights are adaptive — loaded from PostgreSQL agent_weights table                      │
│  Updated after every closed trade by feedback-service                                   │
│                                                                                         │
│  Gate: weighted_confidence ≥ 0.60 to forward                                            │
│                                                                                         │
│  → publish  ensemble.decision  routing_key="risk"                                       │
│  → cache    Redis  "ensemble:{symbol}"  TTL=300s                                        │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                        ensemble.decision (direct exchange)
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 4 — RISK VALIDATION                                  risk-engine :8010 (Java)     │
│                                                                                         │
│  @RabbitListener("ensemble.decision")                                                   │
│                                                                                         │
│  Checks:                                                                                │
│    ✓ action must be BUY or SELL (HOLD dropped)                                          │
│    ✓ confidence ≥ 0.60                                                                  │
│    ✓ price > 0 and ATR > 0                                                              │
│                                                                                         │
│  Position sizing:                                                                       │
│    stop_loss   = price ± ATR × 2                                                        │
│    take_profit = price ± ATR × 3                                                        │
│    max_risk_capital = portfolio × 0.02  (2% max risk per trade)                         │
│    shares_from_risk = max_risk_capital / stop_distance                                  │
│    max_position_shares = (portfolio × 0.05) / price  (5% max position)                  │ 
│    position_size = min(shares_from_risk, max_position_shares)                           │
│                                                                                         │
│  → publish  risk.validated  routing_key="trade"                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                          risk.validated (direct exchange)
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 5 — TRADE EXECUTION                               trade-executor :8011 (Java)     │
│                                                                                         │
│  @RabbitListener("risk.validated")                                                      │
│                                                                                         │
│  Mode: PAPER_TRADING_MODE=true (default)                                                │
│                                                                                         │
│  ┌───────────────────────────────────┐   ┌─────────────────────────────────────────┐    │
│  │  PaperTradingService              │   │  GrowwOrderService (live)               │    │
│  │  simulate fill at price + 0.05%   │   │  POST /order/create to Groww REST API   │    │
│  │  slippage                         │   │  @Retryable: 3 attempts, 2× backoff     │    │
│  │  instant FILLED status            │   │  reads actual fill price from response  │    │
│  └───────────────────────────────────┘   └─────────────────────────────────────────┘    │
│                          │                                   │                          │
│                          └──────────────┬────────────────────┘                          │
│                                         ▼                                               │
│                              TradeOutcome (tradeId, symbol, action,                     │
│                               fillPrice, fillQty, stopLoss, takeProfit,                 │
│                               paperTrade, executedAt, portfolioValue)                   │
│                                         │                                               │
│  → publish  trade.outcomes  (fanout exchange)                                           │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                          trade.outcomes (fanout exchange)
                                binds to:
               trade.outcomes.feedback ──┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 6 — FEEDBACK LEARNING                              feedback-service :8012         │
│                                                                                         │
│  consume trade.outcomes.feedback                                                        │
│                                                                                         │
│  ① Store trade record → PostgreSQL trade_records                                        │
│      (symbol, action, fill_price, pnl, agent_signals JSONB, market_context JSONB)       │
│                                                                                         │
│  ② Update agent weights → PostgreSQL agent_weights                                      │
│      Per agent:                                                                         │
│        if agent_signal == outcome_direction:  w += 0.05 × (1 - w)  ← reward             │
│        else:                                  w -= 0.05 × w         ← penalise          │
│      clamp each weight to [0.05, 0.60]                                                  │
│      normalize all weights to sum = 1.0                                                 │
│                                                                                         │
│  ④ After every 500 trades → publish model.retrain (direct exchange)                     │
│                                                                                         │
│  Ensemble engine reads updated weights from PostgreSQL on every decision                │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                         │
                         model.retrain (direct exchange)
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  STEP 7 — MODEL RETRAINING                               model-trainer :8013            │
│                                                                                         │
│  Triggers:                                                                              │
│    • RabbitMQ model.retrain.trainer (from feedback-service at 500 trades)               │
│    • Scheduled: every 24 hours                                                          │
│    • Manual: POST /train                                                                │
│                                                                                         │
│  ┌──────────────────────────────────────────┐                                           │
│  │ XGBoost Trainer                          │                                           │
│  │  fetch OHLCV from TimescaleDB (365 days) │                                           │
│  │  compute 12 indicators per candle        │                                           │
│  │  label: close[t+1]/close[t] > 1.005→BUY  │                                           │
│  │                               < 0.995→SELL                                           │
│  │                               else →HOLD │                                           │
│  │  train XGBClassifier (3-class)           │                                           │
│  │  log accuracy + params to MLflow         │                                           │
│  │  if accuracy ≥ 0.52 → register model     │                                           │
│  │    "technical-xgboost"                   │                                           │
│  └──────────────────────────────────────────┘                                           │
│                                                                                         │
│  ┌──────────────────────────────────────────┐                                           │
│  │ PPO RL Trainer                           │                                           │
│  │  fetch OHLCV from TimescaleDB            │                                           │
│  │  create TradingEnv (obs=10, act=3)       │                                           │
│  │  train PPO for 200,000 steps             │                                           │
│  │  evaluate Sharpe on hold-out set         │                                           │
│  │  log sharpe_ratio to MLflow              │                                           │
│  │  if Sharpe ≥ 1.0 → register model        │                                           │
│  │    "rl-trading-policy"                   │                                           │
│  └──────────────────────────────────────────┘                                           │
│                                                                                         │
│  Models registered to MLflow :5000                                                      │
│  technical-agent + rl-agent reload on next inference call                               │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Infrastructure Dependencies

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         SHARED INFRASTRUCTURE                              │
│                                                                            │
│  ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────┐   │
│  │  TimescaleDB :5432  │   │   MongoDB :27017    │   │  Redis :6379    │   │
│  │  (PostgreSQL ext.)  │   │                     │   │                 │   │
│  │  tables:            │   │  collections:       │   │  keys:          │   │
│  │  • ohlcv            │   │  • news_articles    │   │  • tick:{sym}   │   │
│  │    (hypertable)     │   │    (indexed by      │   │  • ensemble:{s} │   │
│  │  • agent_weights    │   │     symbol+date)    │   │                 │   │
│  │  • trade_records    │   │                     │   │  TTL:           │   │
│  │  • session_decisions│   │                     │   │  • tick: 60s    │   │
│  └─────────────────────┘   └─────────────────────┘   │  • ensemble:300s│   │
│                                                      └─────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  RabbitMQ :5672  (management UI :15672)                              │  │
│  │                                                                      │  │
│  │  Exchanges (8):                  Queues (17):                        │  │
│  │  • market.data     (fanout) ──►  market.data.technical               │  │
│  │                             ──►  market.data.sentiment               │  │
│  │                             ──►  market.data.macro                   │  │
│  │                             ──►  market.data.pattern                 │  │
│  │                             ──►  market.data.rl                      │  │
│  │  • agent.signals   (direct) ──►  agent.signals (per routing key)     │  │
│  │  • ensemble.decision(direct)──►  ensemble.decision                   │  │
│  │  • risk.validated  (direct) ──►  risk.validated                      │  │
│  │  • trade.orders    (direct) ──►  trade.orders                        │  │
│  │  • trade.outcomes  (fanout) ──►  trade.outcomes.feedback             │  │
│  │  • model.retrain   (direct) ──►  model.retrain.trainer               │  │
│  │  • notifications   (fanout) ──►  notifications.frontend              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  ┌───────────────────────────────┐                                         │
│  │  MLflow :5000                 │                                         │
│  │  • model registry             │                                         │
│  │  • experiment tracking        │                                         │
│  │  • artifact store (local vol) │                                         │
│  │  • SQLite metadata backend    │                                         │
│  │                               │                                         │
│  │  registered models:           │                                         │
│  │  • technical-xgboost          │                                         │
│  │  • rl-trading-policy          │                                         │
│  └───────────────────────────────┘                                         │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Service Port Map

| Service | Port | Language | Role |
|---|---|---|---|
| backend | 8000 | Python | Auth, portfolio, sessions API, AI-engine API, recordings |
| session-runner | — | Python | Same image as backend, `BACKEND_ROLE=runner` — live paper-trading sessions, ML sweeps, counterfactual labelling. No HTTP load |
| market-data-service | 8001 | Python | Ingestion — Groww/Yahoo/NewsAPI → Redis + TimescaleDB + RabbitMQ |
| technical-agent | 8002 | Python | XGBoost + 15-indicator rule engine → BUY/SELL/HOLD |
| sentiment-agent | 8003 | Python | FinBERT on news articles → sentiment signal |
| macro-agent | 8004 | Python | VIX, FX, crude, G-sec → 4-regime macro signal |
| pattern-agent | 8005 | Python | 8 candlestick patterns + HMM regime → signal |
| rl-agent | 8006 | Python | PPO policy (stable-baselines3) → action |
| ensemble-engine | 8007 | Python | 5-agent weighted voting + confidence gating |
| risk-engine | 8010 | Java (Spring Boot) | ATR position sizing, stop/take-profit, 2% max risk |
| trade-executor | 8011 | Java (Spring Boot) | Paper or live Groww order placement |
| feedback-service | 8012 | Python | Trade storage, adaptive weight updates, retrain trigger |
| model-trainer | 8013 | Python | XGBoost + PPO training → MLflow registry |
| stock-scanner | 8014 | Python | Morning pre-open scan + rolling auto-sweep of the NSE equity master; promotes A-grades into sessions |
| autopilot-service | 8015 | Python | Unattended paper/backtest session orchestration (Redis flags, no TTL) |
| groww-feed-service | — | Python | Isolated growwapi live-tick subscriber → Redis. Reads token from `groww:access_token` |
| frontend | 3000 | React / TypeScript | UI — served via nginx at `/neuradex` |
| nginx | 80 | — | Reverse proxy for frontend + API |
| MLflow | 5000 | Python | Model registry + experiment tracking |
| TimescaleDB | 5432 | PostgreSQL ext. | OHLCV time-series + agent weights + trade records |
| MongoDB | 27017 | MongoDB | News articles (indexed by symbol + date) |
| Redis | 6379 | Redis | Live tick cache + ensemble decision cache + shared Groww token |
| RabbitMQ | 5672 | RabbitMQ | Message bus (8 exchanges, 17 queues) |
| Elasticsearch | 9200 | — | Central log store (`neuradex-logs-YYYY.MM.DD` daily indices) |
| Kibana | 5601 | — | Log search & dashboards over the daily indices |
| Adminer | 8080 | — | phpMyAdmin-style DB browser (also proxied at `/neuradex/dev/db/`) — server `postgres`, user `stock_user`, db `stock_prediction_db` |
| InfluxDB | 8086 | — | Metrics time-series |
| Ollama | 11434 | — | Local LLM (llama3.1:8b) for the shadow entry reviewer |

---

## 4. Data Schemas

### ohlcv (TimescaleDB hypertable)
```sql
time       TIMESTAMPTZ   -- partition key
symbol     TEXT
exchange   TEXT          -- 'NSE'
interval   TEXT          -- '1d', '5m', '1h'
open       NUMERIC
high       NUMERIC
low        NUMERIC
close      NUMERIC
volume     BIGINT
UNIQUE (symbol, exchange, interval, time)
```

### agent_weights (PostgreSQL)
```sql
agent      TEXT PRIMARY KEY   -- technical | pattern | sentiment | rl | macro
weight     FLOAT              -- starts: 0.30 | 0.20 | 0.20 | 0.15 | 0.15
updated_at TIMESTAMPTZ
```

### trade_records (PostgreSQL)
```sql
id             SERIAL PRIMARY KEY
symbol         TEXT
action         TEXT         -- BUY | SELL
entry_price    NUMERIC
exit_price     NUMERIC
qty            NUMERIC
pnl            NUMERIC
pnl_pct        NUMERIC
paper_trade    BOOLEAN
agent_signals  JSONB        -- {technical: BUY, sentiment: HOLD, …}
market_context JSONB        -- {vix, regime, macro_score, …}
executed_at    TIMESTAMPTZ
closed_at      TIMESTAMPTZ
```

*(`rl_experiences` — the RL replay buffer — was dropped 2026-08-16: no
producer ever sent the `state` field it required and no consumer read it;
model-trainer trains PPO from OHLCV directly.)*

---

## 5. Agent Signal Message Schema

All agents publish to `agent.signals` exchange with this payload:

```json
{
  "symbol":     "RELIANCE",
  "agent":      "technical",
  "action":     "BUY",
  "confidence": 0.74,
  "indicators": { "rsi": 42.1, "macd": 0.23, "ema_cross": true },
  "timestamp":  "2026-05-24T09:32:00Z"
}
```

---

## 6. Ensemble Decision Message Schema

Published to `ensemble.decision` exchange:

```json
{
  "symbol":             "RELIANCE",
  "final_action":       "BUY",
  "weighted_confidence": 0.68,
  "agreement_score":    0.80,
  "uncertainty":        0.20,
  "agent_votes":        { "technical": "BUY", "sentiment": "BUY", "macro": "HOLD", "pattern": "BUY", "rl": "BUY" },
  "current_price":      2834.50,
  "atr":                45.20,
  "portfolio_value":    100000.0
}
```

---

## 7. Risk Validated Message Schema

Published to `risk.validated` exchange:

```json
{
  "symbol":         "RELIANCE",
  "action":         "BUY",
  "confidence":     0.68,
  "position_size":  17.0,
  "stop_loss":      2744.10,
  "take_profit":    2970.10,
  "current_price":  2834.50,
  "risk_pct":       0.0154,
  "portfolio_value": 100000.0,
  "agent_votes":    { "technical": "BUY", ... },
  "validated_at":   "2026-05-24T09:32:01Z"
}
```

---

## 8. Learning Feedback Loop (Closed Loop)

```
  Trade Executed
       │
       ▼
  feedback-service receives trade.outcomes
       │
       ├──► INSERT into trade_records (PostgreSQL)
       │
       ├──► For each agent:
       │      outcome_direction = PNL > 0 ? BUY/SELL : opposite
       │      if agent_signal == outcome_direction:
       │          weight += 0.05 × (1 - weight)   ← grows toward max
       │      else:
       │          weight -= 0.05 × weight          ← shrinks toward min
       │      clamp to [0.05, 0.60]
       │      normalize all 5 weights to sum = 1.0
       │      UPDATE agent_weights SET weight = … (PostgreSQL)
       │
       └──► Every 500 trades:
              PUBLISH model.retrain → model-trainer starts new XGBoost + PPO run
              Newly registered models are picked up by agents on next inference call
```

---

## 9. Frontend → Service Connections

```
frontend :3000
    │
    ├── /health polls (AgentStatusPanel, every 30s)
    │     ├── http://localhost:8001/health   market-data-service
    │     ├── http://localhost:8002/health   technical-agent
    │     ├── http://localhost:8003/health   sentiment-agent
    │     ├── http://localhost:8004/health   macro-agent
    │     ├── http://localhost:8005/health   pattern-agent
    │     ├── http://localhost:8006/health   rl-agent
    │     ├── http://localhost:8007/health   ensemble-engine
    │     ├── http://localhost:8012/health   feedback-service
    │     └── http://localhost:8013/health   model-trainer
    │
    ├── /models page (ModelRegistry)
    │     └── http://localhost:5000/api/2.0/mlflow/registered-models/list
    │         http://localhost:5000/api/2.0/mlflow/runs/get?run_id=…
    │
    ├── /orders page (Orders)
    │     ├── http://localhost:8012/stats    trade statistics + agent weights
    │     └── http://localhost:8012/trades   trade history
    │
    ├── /ai-engine page
    │     ├── http://localhost:8007/decision/{symbol}   latest ensemble decision
    │     └── http://localhost:8007/weights             current agent weights
    │
    └── /predictions, /portfolio, /risk, /backtest, /paper-trading
          └── http://localhost:8000/*   (legacy backend during transition)
```

---

## 10. Startup Order

Services must start in this sequence (handled by Docker healthchecks + `depends_on`):

```
1. postgres (TimescaleDB)   — healthcheck: pg_isready
2. mongodb                  — healthcheck: mongosh ping
3. redis                    — healthcheck: redis-cli ping
4. rabbitmq                 — healthcheck: rabbitmq-diagnostics check_port_connectivity
5. mlflow                   — (started after postgres)
6. market-data-service      — declares ALL RabbitMQ exchanges + queues on startup
7. technical-agent          — (consumes market.data.technical)
   sentiment-agent          — (consumes market.data.sentiment)   ← parallel
   macro-agent              — (consumes market.data.macro)        ← parallel
   pattern-agent            — (consumes market.data.pattern)      ← parallel
   rl-agent                 — (consumes market.data.rl)           ← parallel
8. ensemble-engine          — (consumes agent.signals)
9. risk-engine              — (consumes ensemble.decision)
10. trade-executor          — (consumes risk.validated)
11. feedback-service        — (consumes trade.outcomes.feedback)
12. model-trainer           — (consumes model.retrain.trainer, runs scheduled loop)
13. backend (legacy)        — auth + existing endpoints
14. frontend                — React app
```

`depends_on` ordering is only honoured by `compose up`. When the Docker daemon
restarts the stack on host boot (`restart: unless-stopped`), every container
starts in parallel — services that read state exactly once at boot must retry
(the scanner's `warm_state` and the Groww token keeper both do).

---

## 11. Observability — Logging Pipeline

Every Python service logs **structured JSON to stdout** and ships the same
records to **Elasticsearch** in daily indices (`neuradex-logs-YYYY.MM.DD`),
searchable in Kibana (:5601). Each document carries `service`, `level`,
`logger`, `message`, `@timestamp`, plus any `extra={}` fields.

```
service code ── logging.* ──► root logger
                                ├─► StreamHandler (JSON) ──► stdout (docker logs)
                                └─► _ElasticsearchHandler ──► queue ──► daemon thread
                                                              └─► POST /_bulk (batch 50, 2s flush)
```

- **Canonical handler**: `shared/python/elk_logger.py`, copied into each
  microservice by `python scripts/sync_shared_python.py` (build contexts are
  isolated — never edit the per-service copies). The backend/session-runner
  variant lives at `backend/app/utils/elk_logger.py`.
- **Service name**: `SERVICE_NAME` env var (set per service in
  docker-compose). The backend image defaults to `neuradeX-backend`;
  session-runner overrides it to `session-runner` so the two are separable
  in Kibana.
- **Self-healing (2026-08-16)**: libraries that reconfigure logging can
  `close()` the ES handler while it stays attached to the root logger; the
  worker thread then exits cleanly and every subsequent record is dropped
  silently — ensemble-engine, rl-agent and technical-agent shipped ~zero
  documents for weeks this way. The handler now revives a dead worker on the
  next `emit()` and reports bulk failures (HTTP errors *and* per-item mapping
  rejections) to stderr, rate-limited to one report per 5 minutes.
- **Not shipped to ES (by design)**: `risk-engine` and `trade-executor`
  (Java/Spring — plain text to stdout only) and `groww-feed-service`
  (deliberately minimal image with a 256 MB cap; adding an ES-shipping
  thread fights its restart-on-leak design). Use `docker logs` for these.
- Quick health check — docs per service for a given day:
  `GET :9200/neuradex-logs-YYYY.MM.DD/_search` with a `terms` agg on
  `service.keyword`. A service that logs to stdout but shows zero ES docs
  is wedged — check its stderr for `elk_logger[...]` reports.

---

*Architecture version: 2.1 — Phases 1–5 complete + scanner/autopilot/session-runner + ELK hardening*
*Last updated: 2026-08-16*
