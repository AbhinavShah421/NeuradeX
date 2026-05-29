# NeuradeX — Stock Prediction AI: System Requirements & Architecture

> **Living Document** — Every architectural decision, component change, or new service MUST be reflected here before implementation begins. All verification steps run against this document.

---

## Table of Contents

1. [Vision & Goals](#1-vision--goals)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Execution Flow](#3-execution-flow)
4. [Microservice Structure](#4-microservice-structure)
5. [Communication Layer — RabbitMQ](#5-communication-layer--rabbitmq)
6. [Component Specifications](#6-component-specifications)
   - [6.1 Market Data Layer](#61-market-data-layer)
   - [6.2 Technical Analysis Agent](#62-technical-analysis-agent)
   - [6.3 Sentiment Agent](#63-sentiment-agent)
   - [6.4 Macro Intelligence Agent](#64-macro-intelligence-agent)
   - [6.5 Pattern Recognition Agent](#65-pattern-recognition-agent)
   - [6.6 Reinforcement Learning Agent](#66-reinforcement-learning-agent)
   - [6.7 Ensemble Decision Engine](#67-ensemble-decision-engine)
   - [6.8 Risk Management AI](#68-risk-management-ai)
   - [6.9 Trade Execution Engine](#69-trade-execution-engine)
   - [6.10 Feedback Learning System](#610-feedback-learning-system)
   - [6.11 Model Registry & Experiment Tracking](#611-model-registry--experiment-tracking)
   - [6.12 Backtesting Engine](#612-backtesting-engine)
7. [Technology Stack](#7-technology-stack)
8. [Data Storage Strategy](#8-data-storage-strategy)
9. [API Contracts](#9-api-contracts)
10. [Frontend Requirements](#10-frontend-requirements)
11. [Infrastructure & DevOps](#11-infrastructure--devops)
12. [Current State vs Target State](#12-current-state-vs-target-state)
13. [Implementation Phases](#13-implementation-phases)
14. [Verification Checkpoints](#14-verification-checkpoints)

---

## 1. Vision & Goals

**NeuradeX** is a fully autonomous, multi-agent AI trading system that:

- Ingests live and historical market data from multiple sources
- Runs parallel specialized AI agents (Technical, Sentiment, Macro, Pattern, RL)
- Produces a weighted ensemble decision with confidence scores
- Validates every decision through a Risk Management AI before execution
- Places real orders via broker APIs (Groww, Zerodha Kite)
- Continuously learns from trade outcomes via online/adaptive learning
- Tracks all model versions, accuracy, and performance in a registry
- Provides a real-time React dashboard for monitoring and control

**Golden Rule:** A mediocre strategy with great risk management beats a brilliant strategy with poor risk management.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Market Data Layer                      │
│  (Live feeds · Historical OHLCV · News · Social Media)   │
└───────────────┬──────────────┬──────────────────────────┘
                │              │              │
                ▼              ▼              ▼
   ┌─────────────────┐ ┌──────────────┐ ┌────────────────┐
   │ Technical Agent │ │ Sentiment    │ │ Macro AI Agent │
   │                 │ │ Agent        │ │                │
   └────────┬────────┘ └──────┬───────┘ └───────┬────────┘
            │                 │                  │
            └────────┬────────┘                  │
                     │           ┌────────────────┘
                     ▼           ▼
          ┌─────────────────────────┐
          │  Ensemble Decision      │
          │  Engine                 │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │  Risk Management AI     │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │  Trade Executor         │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │  Feedback Learning      │
          │  & Model Trainer        │
          └─────────────────────────┘
```

Additionally running in parallel:
- **Pattern Recognition Agent** → feeds into Ensemble
- **Reinforcement Learning Agent** → feeds into Ensemble + updates from Feedback
- **Backtesting Engine** → validates strategies before live deployment
- **Model Registry** → tracks all agent model versions and metrics

---

## 3. Execution Flow

```
Step 1  → Market Data Layer ingests tick/candle data for target symbol
Step 2  → All agents analyze in PARALLEL (Technical, Sentiment, Macro, Pattern, RL)
Step 3  → Each agent emits: { signal, confidence, reasoning, indicators }
Step 4  → Ensemble Decision Engine aggregates weighted votes → final_action + confidence
Step 5  → Risk Management AI validates: stop-loss, position size, max drawdown
Step 6  → If approved → Trade Executor places order via broker API
Step 7  → Trade outcome stored (P&L, context, confidence, agent signals)
Step 8  → Feedback Learning System records outcome → updates agent weights
Step 9  → RL Agent updates its Q-table / policy from new experience
Step 10 → Model Retraining triggered when enough new data accumulates
Step 11 → Ensemble weights adapt to current market regime
```

Every step publishes a **RabbitMQ event** so downstream services react asynchronously.

---

## 4. Microservice Structure

```
/
├── api-gateway/            ← Java Spring Boot  — routing, auth, rate limiting
├── market-data-service/    ← Python            — data ingestion, normalization, streaming
├── technical-agent/        ← Python            — TA indicators + ML models
├── sentiment-agent/        ← Python            — NLP, FinBERT, news/social scoring
├── macro-agent/            ← Python            — macro indicators, FII/DII flows
├── pattern-agent/          ← Python            — CNN/HMM candlestick pattern detection
├── rl-agent/               ← Python            — PPO/DQN/SAC reinforcement learning
├── ensemble-engine/        ← Python            — weighted voting, meta-model
├── risk-engine/            ← Java Spring Boot  — risk rules, position sizing, VaR
├── trade-executor/         ← Java Spring Boot  — broker APIs, order lifecycle
├── feedback-service/       ← Python            — outcome storage, weight updates
├── model-trainer/          ← Python            — offline/online retraining pipeline
├── backtesting-engine/     ← Python            — strategy simulation, walk-forward
├── notification-service/   ← Java Spring Boot  — alerts via email, WhatsApp, WebSocket
├── dashboard-ui/           ← React + TypeScript — real-time monitoring and control
└── docker-compose.yml      ← Orchestration
```

**Language decisions:**
| Layer | Language | Why |
|---|---|---|
| API Gateway, Risk Engine, Trade Executor | Java Spring Boot | High throughput, type-safety, battle-tested broker integration |
| All AI/ML agents, data services | Python | scikit-learn, PyTorch, Stable-Baselines3, FinBERT, ta library |
| Frontend | React + TypeScript | Existing, works well |
| Inter-service messaging | RabbitMQ | Decoupling, retry, dead-letter queues |

---

## 5. Communication Layer — RabbitMQ

### Exchanges & Queues

| Exchange | Type | Purpose |
|---|---|---|
| `market.data` | fanout | Broadcasts new tick/candle to all agent queues |
| `agent.signals` | direct | Each agent publishes its signal |
| `ensemble.decision` | direct | Ensemble publishes final decision |
| `risk.validated` | direct | Risk engine publishes approved/rejected decision |
| `trade.orders` | direct | Trade executor receives orders |
| `trade.outcomes` | fanout | Broadcasts trade result to feedback + RL agent |
| `model.retrain` | direct | Triggers retraining pipeline |
| `notifications` | fanout | Broadcasts alerts to notification service |

### Message Envelope (all messages)

```json
{
  "event_id": "uuid-v4",
  "timestamp": "ISO-8601",
  "service": "technical-agent",
  "version": "1.0",
  "payload": { ... }
}
```

### Agent Signal Payload

```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "signal": "BUY | SELL | HOLD",
  "confidence": 0.78,
  "reasoning": "RSI crossed 30 from below, MACD bullish crossover",
  "indicators": {
    "rsi": 31.4,
    "macd": 0.23,
    "ema_20": 2412.5
  },
  "agent": "technical"
}
```

### Ensemble Decision Payload

```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "final_action": "BUY",
  "weighted_confidence": 0.72,
  "agent_votes": {
    "technical":  { "signal": "BUY",  "confidence": 0.78, "weight": 0.30 },
    "sentiment":  { "signal": "BUY",  "confidence": 0.65, "weight": 0.20 },
    "macro":      { "signal": "HOLD", "confidence": 0.50, "weight": 0.15 },
    "pattern":    { "signal": "BUY",  "confidence": 0.80, "weight": 0.20 },
    "rl":         { "signal": "BUY",  "confidence": 0.70, "weight": 0.15 }
  },
  "uncertainty": 0.18,
  "regime": "TRENDING_BULLISH"
}
```

### Risk Validated Payload

```json
{
  "decision_id": "uuid",
  "approved": true,
  "rejection_reason": null,
  "position_size": 10,
  "stop_loss": 2380.0,
  "take_profit": 2500.0,
  "risk_score": 0.32,
  "max_risk_pct": 2.0
}
```

---

## 6. Component Specifications

---

### 6.1 Market Data Layer

**Service:** `market-data-service` (Python)

**Responsibilities:**
- Collect live tick data via WebSockets
- Fetch historical OHLCV candles (1m, 5m, 15m, 1h, 1d)
- Normalize data from multiple sources to unified schema
- Stream to RabbitMQ `market.data` exchange
- Cache latest tick in Redis (`tick:{symbol}`)
- Persist OHLCV candles in TimescaleDB (PostgreSQL time-series extension)
- Collect options chain data (OI, IV, PCR)
- Fetch news and social sentiment raw text

**Data Sources:**
| Source | Data Type | Priority |
|---|---|---|
| Groww API | Live quotes, OHLCV, portfolio | Primary (Indian markets) |
| Zerodha Kite | Live tick, order book | Secondary |
| Alpha Vantage | Historical OHLCV, fundamentals | Tertiary |
| Yahoo Finance (`yfinance`) | Historical fallback | Fallback |
| NSE/BSE official feeds | Index data, FII/DII | Index/macro |
| NewsAPI | Financial news headlines | Sentiment input |
| Reddit API (PRAW) | Social sentiment raw text | Sentiment input |

**Unified OHLCV Schema:**
```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "timestamp": "2026-05-22T09:15:00+05:30",
  "open": 2410.0,
  "high": 2425.5,
  "low": 2405.0,
  "close": 2418.0,
  "volume": 1234567,
  "oi": null,
  "source": "groww"
}
```

**Tech Stack:**
- Python + `aiohttp` for async HTTP
- `websockets` for live feed
- Redis for tick cache
- PostgreSQL (TimescaleDB hypertable) for OHLCV persistence
- RabbitMQ producer via `aio-pika`

**Verification:**
- [ ] Live tick for RELIANCE appears in Redis within 1s of market tick
- [ ] 1-year historical candles stored in TimescaleDB
- [ ] News articles fetched and stored raw in MongoDB
- [ ] RabbitMQ `market.data` exchange receives message per tick

---

### 6.2 Technical Analysis Agent

**Service:** `technical-agent` (Python)

**Responsibilities:**
- Subscribe to `market.data` queue
- Compute technical indicators using `ta` library
- Run ML inference (XGBoost, LightGBM, LSTM)
- Publish signal to `agent.signals` with routing key `technical`

**Indicators Computed:**
| Category | Indicators |
|---|---|
| Trend | EMA (9, 20, 50, 200), SMA, DEMA, TEMA |
| Momentum | RSI (14), MACD (12,26,9), Stochastic, Williams %R, CCI |
| Volatility | Bollinger Bands (20,2), ATR, Keltner Channels |
| Volume | VWAP, OBV, Volume SMA, MFI |
| Price Action | Support/Resistance, Pivot Points |

**ML Models:**
| Model | Input | Output | Framework |
|---|---|---|---|
| XGBoost classifier | 30 indicator features, last 5 candles | BUY/SELL/HOLD probability | `xgboost` |
| LightGBM classifier | Same + volume features | BUY/SELL/HOLD probability | `lightgbm` |
| LSTM (seq2seq) | 60-candle OHLCV sequences | Next-candle direction probability | `torch` |
| Transformer (time-series) | 128-candle sequences | Multi-step price forecast | `torch` |

**Output:**
```json
{
  "signal": "BUY",
  "confidence": 0.78,
  "reasoning": "RSI(14)=31 crossed 30 from below; MACD bullish crossover; price above EMA200",
  "indicators": { "rsi": 31.4, "macd_hist": 0.23, "bb_position": 0.12 },
  "model_votes": { "xgboost": "BUY", "lgbm": "BUY", "lstm": "HOLD" }
}
```

**Verification:**
- [ ] Agent subscribes to `market.data` and processes within 500ms
- [ ] All indicators computed correctly against `ta` library reference values
- [ ] XGBoost model loaded from MLflow registry, not hardcoded path
- [ ] Published signal appears on `agent.signals` queue with routing key `technical`
- [ ] Unit tests cover each indicator computation

---

### 6.3 Sentiment Agent

**Service:** `sentiment-agent` (Python)

**Responsibilities:**
- Subscribe to raw news/social data from MongoDB
- Score each article/post using FinBERT
- Produce aggregate bullish/bearish score per symbol
- Detect events: earnings surprises, regulatory actions, analyst upgrades
- Compute fear/greed index
- Publish signal to `agent.signals` with routing key `sentiment`

**NLP Models:**
| Model | Task | Source |
|---|---|---|
| FinBERT | Financial sentence classification (positive/negative/neutral) | HuggingFace `ProsusAI/finbert` |
| Llama (via Ollama) | Narrative summarization, event extraction | Local Ollama instance |
| GPT-based summarizer | Earnings call analysis | OpenAI API (optional) |

**Scoring:**
```
bullish_score = Σ(positive_confidence × source_weight) / total_articles
bearish_score = Σ(negative_confidence × source_weight) / total_articles
net_sentiment = bullish_score - bearish_score  # range [-1, +1]
```

**Source Weights:**
| Source | Weight |
|---|---|
| Reuters / Bloomberg (via NewsAPI) | 1.0 |
| Economic Times / Moneycontrol | 0.8 |
| Twitter/X | 0.5 |
| Reddit r/IndiaInvestments | 0.4 |

**Verification:**
- [ ] FinBERT model downloads and runs locally (no API key needed)
- [ ] Sentiment score for a known bullish headline returns > 0.6
- [ ] Event detection flags "rate hike" as bearish macro event
- [ ] Published signal has `confidence` derived from article volume + score magnitude

---

### 6.4 Macro Intelligence Agent

**Service:** `macro-agent` (Python)

**Responsibilities:**
- Track macro indicators on a schedule (not per-tick)
- Determine overall market regime
- Publish macro context to Ensemble Engine

**Tracked Indicators:**
| Indicator | Source | Frequency |
|---|---|---|
| RBI interest rate | RBI website scraper | Monthly |
| CPI / WPI inflation | MOSPI data | Monthly |
| FII / DII flows | NSE bhav copy | Daily |
| USD/INR exchange rate | Yahoo Finance | Daily |
| Crude oil price (Brent) | Yahoo Finance (`BZ=F`) | Daily |
| VIX India | NSE | Daily |
| 10-year G-sec yield | NSE / RBI | Daily |
| US Fed Funds Rate | FRED API | As-released |

**Output:**
```json
{
  "signal": "HOLD",
  "confidence": 0.55,
  "regime": "RISK_OFF",
  "macro_context": {
    "rbi_rate": 6.5,
    "fii_net_buy_cr": -1200,
    "usd_inr": 83.4,
    "crude_brent": 78.2,
    "india_vix": 16.8
  },
  "reasoning": "FII selling ₹1200Cr; VIX elevated; crude rising — risk-off regime"
}
```

**Verification:**
- [ ] Macro data refreshes daily without manual trigger
- [ ] FII/DII data parsed correctly from NSE bhav copy
- [ ] Regime classifier outputs one of: `RISK_ON`, `RISK_OFF`, `NEUTRAL`, `HIGH_VOLATILITY`
- [ ] Macro signal published to `agent.signals` with routing key `macro`

---

### 6.5 Pattern Recognition Agent

**Service:** `pattern-agent` (Python)

**Responsibilities:**
- Detect classic candlestick patterns (doji, engulfing, hammer, etc.)
- Identify chart patterns (head & shoulders, triangle, wedge, flag)
- Detect breakout setups and volatility squeezes
- Classify market regime via Hidden Markov Models
- Publish signal to `agent.signals` with routing key `pattern`

**Models:**
| Model | Task | Framework |
|---|---|---|
| Rule-based scanner | Classic candlestick patterns (30+) | `ta` / custom |
| CNN on candle images | Visual pattern recognition | `torch` (ResNet-based) |
| Transformer | Historical similarity search | `torch` |
| Hidden Markov Model | Regime detection (bull/bear/sideways) | `hmmlearn` |

**Verification:**
- [ ] Bullish engulfing pattern detected correctly on known test candle sequence
- [ ] HMM classifies a sideways range correctly
- [ ] Breakout detection fires when price closes above 20-day high with volume > 1.5x SMA

---

### 6.6 Reinforcement Learning Agent

**Service:** `rl-agent` (Python)

**Responsibilities:**
- Maintain a trading policy trained via RL
- Receive market state vector from Market Data Layer
- Output action (BUY/SELL/HOLD) with probability distribution
- Update policy using outcomes from Feedback Service
- Publish signal to `agent.signals` with routing key `rl`

**State Vector:**
```
[normalized_price, rsi, macd, bb_position, vix, fii_flow,
 sentiment_score, macro_regime_encoded, last_action, pnl_pct,
 days_in_trade, volatility_7d]
```

**Action Space:** `{0: HOLD, 1: BUY, 2: SELL}`

**Reward Function:**
```
reward = profit_pct - (0.5 × max_drawdown_pct) - (0.1 × trade_cost_pct)
```

**Algorithms (in order of deployment):**
| Algorithm | Library | When to use |
|---|---|---|
| DQN | `stable_baselines3` | Initial training (simpler) |
| PPO | `stable_baselines3` | Production (more stable) |
| SAC | `stable_baselines3` | Continuous action space (position sizing) |

**Training Data:** Historical OHLCV from PostgreSQL + trade outcomes from Feedback Service.

**Verification:**
- [ ] Environment implements `gym.Env` interface correctly
- [ ] Agent trains for 100k steps without NaN rewards
- [ ] Backtested Sharpe ratio > 1.0 before promoting to production
- [ ] Policy loads from MLflow registry, not hardcoded file

---

### 6.7 Ensemble Decision Engine

**Service:** `ensemble-engine` (Python)

**Responsibilities:**
- Subscribe to all agent signals on `agent.signals` queue
- Wait for all 5 agents (Technical, Sentiment, Macro, Pattern, RL) to publish within a timeout window (5 seconds)
- Aggregate signals using weighted voting
- Compute uncertainty score
- Detect agent disagreement (conflict score)
- Publish final decision to `ensemble.decision` exchange

**Aggregation Methods:**
| Method | When Used |
|---|---|
| Weighted voting | Default — weights from learning system |
| Bayesian ensemble | When uncertainty is high |
| Meta-model (LightGBM) | After 1000+ labeled outcomes available |

**Agent Weights (initial defaults, adaptive thereafter):**
| Agent | Default Weight |
|---|---|
| Technical | 0.30 |
| Pattern | 0.20 |
| Sentiment | 0.20 |
| RL | 0.15 |
| Macro | 0.15 |

**Conflict Handling:**
- If `agreement_score < 0.5` → downgrade confidence by 20%, flag as `HIGH_UNCERTAINTY`
- If all 5 agents disagree → emit `HOLD` regardless of majority

**Verification:**
- [ ] Waits for all agents, uses last cached signal if an agent times out
- [ ] Weighted voting produces correct result on known test inputs
- [ ] Conflict score correctly reflects 3v2 splits vs 5-0 consensus
- [ ] Published to `ensemble.decision` within 6 seconds of market tick

---

### 6.8 Risk Management AI

**Service:** `risk-engine` (Java Spring Boot)

**Responsibilities:**
- Subscribe to `ensemble.decision` queue
- Validate decision against portfolio-level risk rules
- Compute position size using volatility-based sizing (Kelly Criterion / ATR-based)
- Set stop-loss and take-profit levels
- Check max drawdown budget remaining
- Publish approved/rejected decision to `risk.validated`

**Risk Rules:**
| Rule | Default | Configurable |
|---|---|---|
| Max position size (% of portfolio) | 5% | Yes |
| Max risk per trade (% of portfolio) | 2% | Yes |
| Max daily drawdown | 5% | Yes |
| Max open positions | 10 | Yes |
| Minimum confidence to trade | 0.60 | Yes |
| Max sector concentration | 30% | Yes |

**Position Sizing:**
```
atr_stop_distance = ATR(14) × 2.0
risk_amount = portfolio_value × max_risk_pct
position_size = floor(risk_amount / atr_stop_distance)
```

**Risk Metrics Computed:**
- Value at Risk (VaR 95%, 99%)
- Conditional VaR (CVaR / Expected Shortfall)
- Portfolio Beta
- Sharpe Ratio, Sortino Ratio
- Max Drawdown

**Verification:**
- [ ] A trade exceeding 5% position size is rejected
- [ ] Stop-loss is always set (never `null` on approved trades)
- [ ] VaR computation matches reference formula on test portfolio
- [ ] Stress test scenarios (2008 crash, 2020 COVID) produce expected drawdown estimates

---

### 6.9 Trade Execution Engine

**Service:** `trade-executor` (Java Spring Boot)

**Responsibilities:**
- Subscribe to `risk.validated` queue (approved decisions only)
- Place orders via broker APIs
- Handle order lifecycle: PENDING → OPEN → FILLED / REJECTED / CANCELLED
- Implement retry with exponential backoff on broker errors
- Minimize slippage via limit orders with a configurable tolerance
- Publish trade outcome to `trade.outcomes`

**Broker Integrations:**
| Broker | API | Status |
|---|---|---|
| Groww | Groww Public API (REST) | Implemented (Python client exists — port to Java) |
| Zerodha Kite | Kite Connect API | Planned Phase 2 |
| Paper Trading | Internal simulated broker | Always available |

**Order Types Supported:**
- Market Order
- Limit Order (default — reduces slippage)
- Stop-Loss Order
- Stop-Loss Market Order

**Order State Machine:**
```
CREATED → SUBMITTED → ACKNOWLEDGED → FILLED
                    ↘ REJECTED
         ↘ TIMEOUT → CANCELLED
```

**Verification:**
- [ ] Paper trading mode executes without touching real broker
- [ ] Retry fires on HTTP 503 from broker, max 3 attempts
- [ ] FILLED order publishes to `trade.outcomes` with actual fill price
- [ ] Slippage logged: `expected_price` vs `actual_fill_price`

---

### 6.10 Feedback Learning System

**Service:** `feedback-service` (Python)

**Responsibilities:**
- Subscribe to `trade.outcomes`
- Store complete trade record in PostgreSQL
- Compute agent contribution score (which agent's signal was most predictive)
- Update ensemble agent weights via gradient-free optimization
- Trigger RL agent policy update with new experience tuple `(state, action, reward, next_state)`
- Publish `model.retrain` event when training data threshold is reached

**Trade Record Schema:**
```json
{
  "trade_id": "uuid",
  "symbol": "RELIANCE",
  "action": "BUY",
  "entry_price": 2418.0,
  "exit_price": 2476.0,
  "pnl_pct": 2.40,
  "pnl_abs": 580.0,
  "duration_minutes": 127,
  "agent_signals": { ... },
  "ensemble_confidence": 0.72,
  "market_context": { "regime": "TRENDING_BULLISH", "vix": 13.2 },
  "outcome": "WIN",
  "timestamp_open": "...",
  "timestamp_close": "..."
}
```

**Weight Update Rule:**
```
For each agent:
  if agent_signal == outcome_direction:
    agent_weight += learning_rate × (1 - agent_weight)
  else:
    agent_weight -= learning_rate × agent_weight
Normalize: all weights sum to 1.0
```

**Retraining Trigger:** Every 500 new trade records OR every 24 hours, whichever comes first.

**Verification:**
- [ ] Trade record stored completely within 1s of outcome event
- [ ] Weight update moves in correct direction on known test case
- [ ] Weights always sum to 1.0 after update
- [ ] `model.retrain` event fires after 500 records (configurable)

---

### 6.11 Model Registry & Experiment Tracking

**Service:** MLflow (self-hosted in Docker)

**Responsibilities:**
- Version every trained model (XGBoost, LSTM, Transformer, RL policy, Ensemble meta-model)
- Track experiments: hyperparameters, metrics, training data version
- Serve models via MLflow Model Registry REST API
- Allow rollback to previous model version
- Track: accuracy, Sharpe ratio, win rate, max drawdown per model version

**Key Metrics Tracked Per Model:**
| Metric | Description |
|---|---|
| `win_rate` | % of trades where signal direction was correct |
| `sharpe_ratio` | Risk-adjusted return |
| `max_drawdown` | Worst peak-to-trough drop |
| `avg_confidence` | Mean confidence when trading |
| `calibration_error` | How well confidence scores match actual win rate |

**Model Promotion Flow:**
```
STAGING (just trained) → validated by backtesting → PRODUCTION → deprecated
```

**Verification:**
- [ ] MLflow UI accessible at `http://localhost:5000`
- [ ] Every trained model has: version, training_date, dataset_hash, metrics
- [ ] Agents load models from MLflow registry, not local file paths
- [ ] Rollback to previous version completes within 30 seconds

---

### 6.12 Backtesting Engine

**Service:** `backtesting-engine` (Python)

**Responsibilities:**
- Simulate trading strategies on historical data
- Walk-forward validation to prevent overfitting
- Test all agent combinations and ensemble weights
- Generate performance report: Sharpe, CAGR, max drawdown, win rate
- Gate-keep model promotion: only models that pass backtest go to production

**Frameworks:**
| Framework | Use Case |
|---|---|
| `vectorbt` | Fast vectorized backtesting (primary) |
| `backtrader` | Event-driven backtesting for complex strategies |
| Custom walk-forward engine | Sliding window validation |

**Backtest Report Fields:**
```json
{
  "strategy": "ensemble_v3",
  "period": "2020-01-01 to 2025-12-31",
  "total_trades": 847,
  "win_rate": 0.61,
  "cagr": 0.28,
  "sharpe_ratio": 1.82,
  "sortino_ratio": 2.41,
  "max_drawdown": -0.14,
  "calmar_ratio": 2.0,
  "avg_trade_duration_hours": 18.3,
  "profit_factor": 2.1
}
```

**Promotion Gate (minimum thresholds to pass):**
| Metric | Minimum Required |
|---|---|
| Sharpe Ratio | > 1.0 |
| Win Rate | > 52% |
| Max Drawdown | < 20% |
| Total Trades (sample) | > 100 |

**Verification:**
- [ ] SMA crossover on NIFTY 5-year data matches reference backtest output
- [ ] Walk-forward test uses no future data (look-ahead bias check)
- [ ] A strategy below Sharpe 1.0 is blocked from PRODUCTION promotion
- [ ] Backtest completes for 5-year daily data in < 60 seconds

---

## 7. Technology Stack

| Layer | Technology | Version |
|---|---|---|
| **API Gateway** | Java Spring Boot | 3.x |
| **Risk Engine** | Java Spring Boot | 3.x |
| **Trade Executor** | Java Spring Boot | 3.x |
| **Notification Service** | Java Spring Boot | 3.x |
| **AI Agents (all)** | Python + FastAPI | 3.11 / 0.104 |
| **Frontend** | React + TypeScript + Vite | 18 / 5 |
| **Message Broker** | RabbitMQ | 3.12 |
| **Time-Series DB** | PostgreSQL + TimescaleDB | 15 |
| **Document Store** | MongoDB | 6 |
| **Cache / Pub-Sub** | Redis | 7 |
| **Metrics DB** | InfluxDB | 2 |
| **Search / Logs** | Elasticsearch + Kibana | 8.11 |
| **ML Framework** | PyTorch + scikit-learn | 2.x |
| **RL Framework** | Stable-Baselines3 + Ray RLlib | 2.x |
| **NLP / Sentiment** | HuggingFace Transformers (FinBERT) | 4.x |
| **Local LLM** | Ollama (Llama 3.2) | latest |
| **Model Registry** | MLflow | 2.x |
| **Backtesting** | vectorbt + backtrader | latest |
| **Containerization** | Docker + Docker Compose | latest |

---

## 8. Data Storage Strategy

| Data | Store | Why |
|---|---|---|
| User accounts, auth | PostgreSQL | Relational, ACID |
| OHLCV candles (time-series) | PostgreSQL (TimescaleDB hypertable) | Time-series compression, fast range queries |
| Trade records, feedback | PostgreSQL | Relational queries for weight updates |
| Raw news / social text | MongoDB | Unstructured, variable schema |
| Model predictions (history) | MongoDB | Document store suits nested agent signals |
| Live tick cache | Redis | Sub-ms read, TTL-based expiry |
| System metrics, latency | InfluxDB | Time-series metrics for dashboards |
| Application logs | Elasticsearch | Full-text search, Kibana visualization |
| ML models | MLflow artifact store (S3 or local) | Versioned, tagged |

---

## 9. API Contracts

### API Gateway Routes (Java Spring Boot — port 8080)

```
POST   /api/v1/auth/signup
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh

GET    /api/v1/stocks/{symbol}/quote
GET    /api/v1/stocks/{symbol}/candles?interval=5m&from=&to=
GET    /api/v1/stocks/search?q=

GET    /api/v1/predictions/{symbol}
POST   /api/v1/predictions/analyze          ← triggers full pipeline for symbol

GET    /api/v1/portfolio
GET    /api/v1/portfolio/performance
POST   /api/v1/portfolio/alerts

POST   /api/v1/orders                       ← trade-executor
GET    /api/v1/orders/{id}
DELETE /api/v1/orders/{id}

GET    /api/v1/risk/metrics
GET    /api/v1/risk/var
POST   /api/v1/risk/stress-test

GET    /api/v1/backtest/strategies
POST   /api/v1/backtest/run
GET    /api/v1/backtest/{id}/report

GET    /api/v1/agents/status               ← health of all agents
GET    /api/v1/agents/weights             ← current ensemble weights
GET    /api/v1/models                     ← MLflow registry summary
```

### WebSocket Events (Socket.IO — port 8000)

| Event | Direction | Payload |
|---|---|---|
| `tick:{symbol}` | Server → Client | Live quote |
| `signal:{symbol}` | Server → Client | Agent signal update |
| `decision:{symbol}` | Server → Client | Ensemble decision |
| `trade:update` | Server → Client | Order status change |
| `portfolio:update` | Server → Client | Portfolio P&L update |
| `alert:triggered` | Server → Client | Risk alert |

---

## 10. Frontend Requirements

**Stack:** React 18 + TypeScript + Vite + Tailwind CSS

### Pages & Features

| Page | Route | Key Features |
|---|---|---|
| Dashboard | `/` | Live market overview, agent status, recent signals, P&L ticker |
| AI Engine | `/ai-engine` | Real-time ensemble decision panel, agent vote breakdown, confidence meter |
| Predictions | `/predictions` | Per-symbol prediction, agent signals, historical accuracy |
| Portfolio | `/portfolio` | Holdings (live via Groww), allocation chart, P&L |
| Risk Analytics | `/risk` | VaR, stress-test, Sharpe, drawdown charts |
| Backtest | `/backtest` | Strategy selector, date range, results chart |
| Paper Trading | `/paper-trading` | Live intraday session with LLM decisions |
| Orders | `/orders` | Order history, status tracking |
| Model Registry | `/models` | MLflow summary, agent weight history |
| Settings | `/settings` | Broker config, risk limits, notification preferences |
| Login / Signup | `/login` `/signup` | OTP auth, broker linking |

### Real-time Requirements
- All price data updates via Socket.IO (no polling)
- Agent signal panel updates within 2 seconds of a new signal
- Portfolio P&L recalculates on every tick for held symbols

---

## 11. Infrastructure & DevOps

### Docker Compose Services

| Service | Port | Notes |
|---|---|---|
| postgres | 5432 | TimescaleDB extension required |
| mongodb | 27017 | |
| redis | 6379 | |
| rabbitmq | 5672, 15672 | Management UI on 15672 |
| influxdb | 8086 | |
| elasticsearch | 9200 | |
| kibana | 5601 | |
| mlflow | 5000 | New — model registry UI |
| api-gateway | 8080 | Java Spring Boot |
| market-data-service | 8001 | Python |
| technical-agent | 8002 | Python |
| sentiment-agent | 8003 | Python |
| macro-agent | 8004 | Python |
| pattern-agent | 8005 | Python |
| rl-agent | 8006 | Python |
| ensemble-engine | 8007 | Python |
| risk-engine | 8010 | Java Spring Boot |
| trade-executor | 8011 | Java Spring Boot |
| feedback-service | 8012 | Python |
| model-trainer | 8013 | Python |
| backtesting-engine | 8014 | Python |
| notification-service | 8015 | Java Spring Boot |
| frontend | 3000 | React |
| nginx | 80, 443 | Reverse proxy |

### Health Check Standard (all services)

Every service exposes:
```
GET /health
→ { "status": "ok", "service": "technical-agent", "version": "1.0.0", "uptime_s": 3600 }
```

---

## 12. Current State vs Target State

| Component | Current State | Target State | Gap |
|---|---|---|---|
| Market Data Layer | Groww API + Yahoo Finance fallback, no streaming | Unified streaming service, TimescaleDB, Redis tick cache, multiple sources | Needs dedicated service + TimescaleDB |
| Technical Agent | Rule-based indicators, placeholder ML models | Real XGBoost + LSTM inference from MLflow | Train real models, integrate MLflow |
| Sentiment Agent | Simulated random scores | FinBERT NLP on real news/Reddit | FinBERT integration, NewsAPI/Reddit feed |
| Macro Agent | Not implemented | Dedicated macro data fetcher + regime classifier | Build from scratch |
| Pattern Agent | Basic candlestick rules in technical agent | CNN + HMM pattern detector, separate service | Separate service + CNN model |
| RL Agent | Q-table placeholder | PPO/DQN via Stable-Baselines3, gym environment | Build proper env + train policy |
| Ensemble Engine | Single Python class in monolith | Dedicated service, Bayesian ensemble, meta-model | Separate service, real weight learning |
| Risk Management | VaR/CVaR computed in API handler | Dedicated Java Spring Boot service, real-time checks | New Java service |
| Trade Executor | Python Groww client in monolith | Java Spring Boot service, full order lifecycle | New Java service |
| Feedback System | Basic outcome recording, simple weight update | Full trade record + adaptive weight optimization + RL update | Deepen implementation |
| Model Registry | Not implemented | MLflow self-hosted | Add MLflow to docker-compose |
| Backtesting | SMA/RSI/MACD/BB backtest in API | vectorbt + walk-forward + promotion gating | Add vectorbt + walk-forward |
| Communication | Direct HTTP calls | RabbitMQ events between all services | Wire RabbitMQ consumers/producers |
| API Gateway | FastAPI monolith on 8000 | Java Spring Boot API Gateway on 8080 | New Java service |
| Frontend | 11 pages, complete | Add Model Registry page, Orders page, agent status panel | Incremental additions |

---

## 13. Implementation Phases

### Phase 1 — Foundation (Infrastructure + Data) ✅ COMPLETE
**Goal:** Real data flowing through the system via RabbitMQ.

- [x] Add MLflow to docker-compose (port 5000, SQLite backend, local artifact store)
- [x] Add TimescaleDB extension to PostgreSQL (`timescale/timescaledb:latest-pg15`)
- [x] Build `market-data-service` (port 8001): Groww + Yahoo Finance + NewsAPI → Redis + TimescaleDB + RabbitMQ
- [x] Wire all 8 RabbitMQ exchanges + 17 queues (`rabbitmq_setup.py`)
- [x] TimescaleDB hypertable `ohlcv` + `agent_weights` + `trade_records` + `rl_experiences`
- [ ] **VERIFY:** `docker exec stock-prediction-postgres psql -U stock_user -d stock_prediction_db -c "SELECT count(*) FROM ohlcv WHERE symbol='RELIANCE';"`

### Phase 2 — Agent Services (ML + Real Inference) ✅ COMPLETE
**Goal:** Real ML inference from trained models, not simulated outputs.

- [x] `technical-agent` (port 8002): 15 indicators + XGBoost from MLflow + rule-based fallback
- [x] `sentiment-agent` (port 8003): FinBERT (`ProsusAI/finbert`) + MongoDB news consumer + weighted source scoring
- [x] `macro-agent` (port 8004): VIX India, USD/INR, crude oil, G-sec, regime classifier (4 regimes)
- [x] `pattern-agent` (port 8005): 8 candlestick patterns + HMM regime via `hmmlearn`
- [x] `rl-agent` (port 8006): `TradingEnv` gym environment + PPO from MLflow + momentum fallback
- [x] All agents consume from `market.data.{agent}` queue, publish to `agent.signals` with typed routing key
- [x] `model-trainer` (port 8013): XGBoost 3-class classifier + PPO RL trainer; fetches OHLCV from TimescaleDB, logs to MLflow, registers if accuracy ≥ 52% / Sharpe ≥ 1.0; daily scheduled retraining + `model.retrain` RabbitMQ trigger
- [x] CORS middleware added to all Python microservices

### Phase 3 — Ensemble + Risk (Decision Making) ✅ COMPLETE
**Goal:** Fully automated decision pipeline from data to validated trade decision.

- [x] `ensemble-engine` (port 8007): Collects all 5 agents with 5s timeout, weighted voting, conflict detection, confidence gating (min 0.60)
- [x] Adaptive weights from `agent_weights` PostgreSQL table
- [x] Publishes to `ensemble.decision` + Redis cache `ensemble:{symbol}`
- [x] `risk-engine` (port 8010, Java Spring Boot): ATR-based position sizing, stop-loss (ATR×2), take-profit (ATR×3), max 5% position, max 2% risk per trade; validates confidence ≥ 0.60; publishes to `risk.validated`

### Phase 4 — Execution + Feedback (Closing the Loop) ✅ COMPLETE
**Goal:** Real orders placed, outcomes feed back into model improvement.

- [x] `feedback-service` (port 8012): Full trade records in PostgreSQL, adaptive weight updates, RL experience replay buffer, `model.retrain` trigger at configurable threshold
- [x] Weight update rule per REQUIREMENTS.md spec implemented and tested
- [x] RL replay buffer capped at 10k tuples with automatic pruning
- [x] `trade-executor` (port 8011, Java Spring Boot): Paper trading by default (PAPER_TRADING_MODE=true), Groww live trading via REST API with exponential backoff retry; publishes to `trade.outcomes` fanout

### Phase 5 — Frontend Polish ✅ COMPLETE
**Goal:** Clean, production-ready interface with full microservice visibility.

- [x] `AgentStatusPanel` component: polls /health on all 9 microservice ports every 30s, green/red status badges with glow
- [x] `ModelRegistry` page (/models): live MLflow REST API integration — shows registered models, versions, accuracy/Sharpe metrics from run data
- [x] `Orders` page (/orders): trade history table from feedback-service + agent weight visualisation + trade stats panel
- [x] Routes `/models` and `/orders` added to App.tsx
- [x] Nav items "Models" and "Orders" added to Layout.tsx NAV array
- [ ] Build `api-gateway` in Java Spring Boot (routing, auth, rate limiting) — deferred to Phase 6
- [ ] Migrate auth to API Gateway (JWT verification at gateway level) — deferred to Phase 6

### Phase 6 — Frontend Architecture Revamp + Unified Trade Flow
**Goal:** Coherent UX where AI Engine is the brain, Backtest/Paper Trading are modes of the same pipeline, Risk is portfolio-aware, and every trade (live, paper, backtest) trains the AI.

#### 6a — Navigation Restructure
- [ ] `AI Engine` becomes single top-level nav item with dropdown containing:
  - `Live Analysis` → `/ai-engine` (current multi-agent analysis page)
  - `AI Agents` → `/ai-engine/agents` (agent status, weights, performance)
  - `Backtesting` → `/ai-engine/backtest` (historical simulation)
  - `Paper Trading` → `/ai-engine/paper-trading` (simulated live trading)
- [ ] Remove `Risk` from top nav — move into `Portfolio` as a tab
- [ ] Remove standalone `AI Agent`, `Backtest`, `Paper Trading` nav links
- [ ] `AIEngineLayout.tsx` wrapper component with sub-nav bar + React Router `<Outlet>`

#### 6b — Unified Trade Flow (Backtest + Paper Trading → Same Pipeline)
**All three modes (backtest, paper, live) MUST flow through the same microservice chain:**
```
Historical/Live Data → Agents (×5) → Ensemble → Risk Engine → Trade Executor (mode flag)
                                                                     ↓
                                              feedback-service ← trade.outcomes
                                                     ↓
                                              model-trainer ← agent weight update + retrain trigger
```
- [ ] `trade_source` column added to `trade_records` table: `LIVE | PAPER | BACKTEST`
- [ ] Backtest sends OHLCV slices through agent pipeline via ensemble-engine `/analyze` endpoint
- [ ] Paper trading uses `PAPER_TRADING_MODE=true` in trade-executor (already implemented)
- [ ] Both backtest + paper trading results feed into feedback-service → model-trainer
- [ ] Trained models from backtest results are promoted via the same MLflow registry gate

#### 6c — Portfolio Risk Tab (Portfolio-Relative Risk)
**Risk is personal — computed from actual user holdings, not generic market risk:**
- [ ] Portfolio page gains a `Risk` tab alongside `Holdings` and `Performance`
- [ ] Risk metrics are portfolio-aware:
  - Concentration risk: each holding's % of total portfolio value
  - Position-level unrealised P&L distribution
  - Max drawdown estimate based on ATR of held positions
  - Diversification score: Herfindahl-Hirschman Index (HHI) on sector/stock weights
  - Risk flag: any position > 10% of portfolio triggers HIGH concentration warning
  - VaR estimate: 95% 1-day VaR based on historical volatility of held stocks
- [ ] Risk page (`/risk`) removed; `<RiskAnalytics />` component embedded as Portfolio tab

#### 6d — Trade Execution History with Step-by-Step Preview
**Every order in history is clickable — shows the full execution trace:**
- [ ] `trade_execution_steps` table in TimescaleDB:
  ```sql
  trade_id     TEXT REFERENCES trade_records(trade_id)
  step_order   INT      -- 1=signal 2=agents 3=ensemble 4=risk 5=executor 6=outcome
  step_name    TEXT     -- MARKET_SIGNAL | AGENT_DECISIONS | ENSEMBLE_VOTE | RISK_GATE | ORDER_FILL | TRADE_OUTCOME
  step_data    JSONB    -- full payload at that step
  occurred_at  TIMESTAMPTZ
  ```
- [ ] Execution steps reconstructed from `agent_signals` + `market_context` stored in `trade_records`
- [ ] `/trades` endpoint in feedback-service returns paginated trade list with all JSONB fields
- [ ] `/trades/{trade_id}` returns single trade with reconstructed execution steps
- [ ] Clicking any order in `/orders` page opens a modal timeline:
  - Step 1: Market Signal (symbol, price, timestamp)
  - Step 2: Agent Decisions (grid of 5 agents with action + confidence)
  - Step 3: Ensemble Vote (final action, agreement score, weight breakdown)
  - Step 4: Risk Gate (position size, stop-loss, take-profit, risk%)
  - Step 5: Order Fill (fill price, slippage, qty, paper/live badge)
  - Step 6: Outcome (P&L, win/loss, reward fed to model)
- [ ] Same execution preview available for backtest orders and paper trading orders
- [ ] Trade history persists across sessions — user can revisit any past trade

#### 6e — Verify Checkpoints
```bash
# All /ai-engine sub-routes load correctly
curl http://localhost:3000/ai-engine/agents
curl http://localhost:3000/ai-engine/backtest
curl http://localhost:3000/ai-engine/paper-trading

# Trade execution steps endpoint
curl http://localhost:8012/trades | jq '.[0].trade_id'
curl http://localhost:8012/trades/{trade_id} | jq '.execution_steps'

# Portfolio risk tab loads
curl http://localhost:8000/api/v1/portfolio | jq '.stocks[].gainPercent'

# Backtest results appear in feedback stats
curl http://localhost:8012/stats | jq '.trade_stats'
```

### Phase 7 — Hardening + Backtesting Gate
**Goal:** Nothing goes to production without passing backtesting gate.

- [ ] Integrate `vectorbt` into `backtesting-engine`
- [ ] Implement walk-forward validation
- [ ] Enforce backtest promotion gate before any model goes to PRODUCTION
- [ ] Load testing: 100 symbols × 5 agents, verify latency < 6s
- [ ] Security audit: API key rotation, secrets via env vars only

---

## 14. Verification Checkpoints

These checks MUST pass before marking any phase complete. Run these after every significant change.

### Data Layer Checks
```bash
# Redis has live tick
redis-cli GET "tick:RELIANCE" | jq .

# TimescaleDB has OHLCV rows
psql -U stock_user -d stock_prediction_db -c \
  "SELECT count(*) FROM ohlcv WHERE symbol='RELIANCE' AND interval='5m';"

# RabbitMQ has received market.data messages
curl -u guest:guest http://localhost:15672/api/queues/%2F/market.data.technical
```

### Agent Signal Checks
```bash
# Each agent must publish a signal within 10s of market tick
# Check via RabbitMQ management UI: http://localhost:15672
# Queue: agent.signals — messages should be flowing

# Verify signal schema
curl http://localhost:8002/health
curl http://localhost:8003/health
```

### Ensemble + Risk Checks
```bash
# Ensemble must produce a decision within 6s of last agent signal
# Risk engine must respond within 500ms of ensemble decision

# Check ensemble output queue
curl -u guest:guest http://localhost:15672/api/queues/%2F/ensemble.decision

# Check risk validated queue
curl -u guest:guest http://localhost:15672/api/queues/%2F/risk.validated
```

### Model Registry Checks
```bash
# MLflow UI accessible
curl http://localhost:5000/health

# Verify technical-agent loads model from registry (not local file)
# Look for MLflow run ID in agent startup logs
docker logs stock-prediction-technical-agent | grep "mlflow"
```

### Trade Execution Checks
```bash
# Paper trading: order appears in internal store, not sent to Groww
# Live trading: order appears in Groww order book

# Verify slippage log
docker logs stock-prediction-trade-executor | grep "slippage"
```

### End-to-End Smoke Test
```bash
# POST a prediction request and verify the full pipeline fires
curl -X POST http://localhost:8080/api/v1/predictions/analyze \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "RELIANCE", "exchange": "NSE"}'

# Expected: Response within 8s containing:
# - ensemble final_action
# - all 5 agent signals
# - risk validation result
# - (if approved) order reference
```

---

*Last updated: 2026-05-22*
*Architecture version: 2.0*
*Status: Phases 1–5 complete. Phase 6 (api-gateway, backtesting gate, hardening) pending.*


Phase 2

Also keep backtesting and paper tarding orders in our db so when the user come again to his account and want to see previously exicuted trade so he can click on the particulor order and see the all preview of exicution of that trade as we show it while it is getting exicuted in papar trading and backtesting
