---
id: ai-engine
title: AI Engine Section
sidebar_label: AI Engine
---

# AI Engine Section (`/ai-engine/*`)

Four pages under the AI Engine nested layout, each exposing a different mode of AI-driven trading.

---

## AI Engine — Ensemble Analysis (`/ai-engine`)

**File:** `frontend/src/pages/AIEngine.tsx`

Runs the full multi-agent ensemble on a stock and records trade outcomes for feedback learning.

### API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | POST | `/api/paper-trading/start` | On "Start Session" | Initialize paper trading session |
| 2 | POST | `/api/ai-engine/analyze` | On "Analyze" | Run multi-agent ensemble vote |
| 3 | POST | `/api/ai-engine/outcome` | On "Record Outcome" | Feed trade result back to learning system |
| 4 | GET | `/api/ai-engine/performance` | On "Performance" tab | Agent weight + accuracy stats |
| 5 | GET | `/api/ai-engine/history` | On "History" tab | Past ensemble predictions |

### Data Flow

```
User enters: symbol, capital
    │
    └─ POST /api/paper-trading/start
           body: { symbol, capital }
           → { session_id, ... }

User clicks Analyze
    │
    └─ POST /api/ai-engine/analyze
           body: { symbol, candles: [...], session_id }
           → {
               final_signal: "BUY",
               confidence: 0.78,
               agent_votes: {
                 technical: { signal, confidence, weight },
                 sentiment: { signal, confidence, weight },
                 pattern:   { signal, confidence, weight },
                 macro:     { signal, confidence, weight },
                 rl:        { signal, confidence, weight }
               },
               reasoning: "..."
             }

User records outcome
    │
    └─ POST /api/ai-engine/outcome
           body: { symbol, signal, entry_price, exit_price,
                   outcome: "PROFIT" | "LOSS" | "BREAKEVEN" }
           → { message: "Outcome recorded" }
```

### Tabs

| Tab | API | Content |
|---|---|---|
| Live Analysis | `/api/ai-engine/analyze` | Voting breakdown pie, agent confidence bars |
| Agent Performance | `/api/ai-engine/performance` | Weight per agent, accuracy over time |
| Prediction History | `/api/ai-engine/history` | Table: date, symbol, signal, outcome |

---

## AI Agent — Ollama LLM Analysis (`/ai-engine/agents`)

**File:** `frontend/src/pages/AIAgent.tsx`

Uses a locally-running Ollama LLM to generate a natural-language analysis of a stock.

### API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/agent/stocks` | On mount | Stocks available for AI analysis |
| 2 | GET | `/api/agent/models` | On mount | Available Ollama models |
| 3 | POST | `/api/agent/analyze/{symbol}` | On "Analyze" | Run LLM analysis |

### Data Flow

```
Page mounts
    │
    ├─ GET /api/agent/stocks   → stocks[] (symbol list)
    └─ GET /api/agent/models   → models[] (ollama model names, e.g. "llama3", "mistral")

User selects: symbol, model
    │
    └─ POST /api/agent/analyze/{symbol}
           body: { model: "llama3" }
           → {
               analysis: "Based on 1-year candlestick data...",
               indicators: { rsi: 58.2, macd: 12.4, ... },
               recommendation: "BUY",
               confidence: 0.71
             }
```

### Notes

- Requires Ollama running locally on the host machine
- Backend proxies request to `http://host.docker.internal:11434`
- Loading steps shown while analysis streams: "Fetching candles → Computing indicators → Calling LLM → Formatting response"

---

## Backtest (`/ai-engine/backtest`)

**File:** `frontend/src/pages/Backtest.tsx`

Two modes: **Strategy Backtest** (historical simulation) and **AI Autopilot** (live candle-by-candle replay with AI).

### API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/backtest/strategies` | On mount | Available strategy list |
| 2 | POST | `/api/backtest/run` | On "Run Backtest" | Execute historical strategy backtest |
| 3 | GET | `/api/backtest/live-signal/{symbol}` | On "Get Signal" | Live signal for a strategy |
| 4 | GET | `/api/backtest/intraday-candles/{symbol}` | On "Load Candles" | Intraday 1-min candles for replay |
| 5 | POST | `/api/backtest/progressive/start` | On "Start Replay" | Initialize progressive replay session |
| 6 | POST | `/api/backtest/progressive/step` | On each "Step" tick | Advance replay by one candle + get AI decision |

### Data Flow — Strategy Backtest

```
Page mounts
    └─ GET /api/backtest/strategies
           → [{ id, name, parameters: { param_name: default_value } }]

User configures: symbol, strategy, startDate, endDate, capital, commission, params
    │
    └─ POST /api/backtest/run
           body: { symbol, strategy_id, start_date, end_date,
                   initial_capital, commission, parameters }
           → {
               equity_curve: [{ date, value }],
               trades: [{ entry_date, exit_date, side, entry_price, exit_price, pnl }],
               metrics: { total_return, sharpe, max_drawdown, win_rate, total_trades }
             }

Chart rendered: equity curve (Lightweight-charts)
               aggregated by auto timeframe: 5m / 10m / 15m / 30m
```

### Data Flow — AI Autopilot (Live Replay)

```
User picks: symbol, date, start_time, capital

GET /api/backtest/intraday-candles/{symbol}
    query: date=2024-01-15
    → [{ time, open, high, low, close, volume }]  (1-min candles)

POST /api/backtest/progressive/start
    body: { symbol, date, capital, start_time }
    → { session_id, initial_state }

─── Loop (user clicks Step or auto-step) ───────────────────
POST /api/backtest/progressive/step
    body: { session_id }
    → {
        candle: { time, open, high, low, close, volume },
        ai_decision: { signal, confidence, reasoning },
        portfolio: { cash, position, value },
        done: false
      }
─── End when done: true ────────────────────────────────────
```

---

## Paper Trading (`/ai-engine/paper-trading`)

**File:** `frontend/src/pages/PaperTrading.tsx`

Live 1-minute paper trading session using real Groww tick data with AI decisions.

### API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/paper-trading/status` | On mount | Market open/closed + session info |
| 2 | POST | `/api/paper-trading/start` | On "Start" | Create new paper trading session |
| 3 | GET | `/api/paper-trading/tick/{symbol}` | Every minute (interval) | Latest quote + AI signal snapshot |
| 4 | POST | `/api/paper-trading/step` | Every minute (interval) | Advance AI agent by one candle |
| 5 | POST | `/api/paper-trading/place-order` | On AI signal or manual | Place paper order |

### Data Flow

```
Page mounts
    └─ GET /api/paper-trading/status
           → { market_open: true, session_id?, ... }

User configures: symbol, capital
    │
    └─ POST /api/paper-trading/start
           body: { symbol, capital }
           → { session_id, symbol, capital, start_time }

─── Every 60 seconds (setInterval) ─────────────────────────
GET /api/paper-trading/tick/{symbol}
    → {
        price: 2834.50,
        signal: "BUY",
        confidence: 0.74,
        indicators: { rsi, macd, bb_upper, bb_lower }
      }

POST /api/paper-trading/step
    body: { session_id }
    → {
        candle: { time, open, high, low, close, volume },
        ai_decision: { signal, confidence },
        portfolio: { cash, position, unrealized_pnl }
      }
─── End of interval ────────────────────────────────────────

[AI auto-decision / manual override]
POST /api/paper-trading/place-order
    body: { session_id, symbol, side: "BUY", quantity: 5, price: 2834.50 }
    → { order_id, status, fill_price }
```

### Chart

Uses Lightweight-charts. Candles are aggregated dynamically:
- `< 60 candles` → show raw 1-min
- `60–120` → aggregate to 5-min
- `120–240` → aggregate to 10-min
- `> 240` → aggregate to 15-min

### State

```typescript
symbol: string
capital: number
session: PaperSession | null
tickInterval: ReturnType<typeof setInterval> | null
liveSignal: LiveSignal | null
liveIndicators: Indicators | null
position: Position | null
closedTrades: Trade[]
```
