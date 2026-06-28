---
id: ai-engine
title: AI Engine Section
sidebar_label: AI Engine
---

# AI Engine Section (`/ai-engine/*`)

Pages under the AI Engine nested layout (`frontend/src/pages/AIEngineLayout.tsx`), each exposing a different mode of AI-driven trading:

| Tab | Route | File |
|---|---|---|
| Live Analysis | `/ai-engine` | `AIEngine.tsx` |
| AI Agents | `/ai-engine/agents` | `AIAgent.tsx` |
| Backtesting | `/ai-engine/backtest` | `Backtest.tsx` |
| Paper Trading | `/ai-engine/paper-trading` | `PaperTrading.tsx` |
| **Agents & Memory** | `/ai-engine/memory` | `PatternMemory.tsx` |
| Live Trading | `/ai-engine/live-trading` | `LiveTrading.tsx` |

> **Note:** the former standalone **AI Models** tab (`/ai-engine/models-control`) was merged into **Agents & Memory**; that route now redirects to `/ai-engine/memory`.

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

---

## Agents & Memory (`/ai-engine/memory`)

**File:** `frontend/src/pages/PatternMemory.tsx`

The unified control + insight page for the **in-process ensemble** (the agents that
power Live Analysis and paper/replay/backtest sessions). It merges what used to be
two separate tabs — *AI Models* (enable/weight controls) and *Pattern Memory*
(learning stats) — into one screen.

### API Calls

| # | Method | Endpoint | When | Purpose |
|---|---|---|---|---|
| 1 | GET | `/api/ai-engine/memory/stats` | On mount | Pattern-memory bank totals, by-action / by-source / top-symbols |
| 2 | GET | `/api/ai-engine/learning/summary` | On mount | Per-agent accuracy + effective weight rankings |
| 3 | GET | `/api/ai-engine/models` | On mount | Enable flag + weight override per agent (`apiService.aiModels()`) |
| 4 | POST | `/api/ai-engine/models` | On toggle / weight change | Enable/disable or pin/clear an agent's weight (`apiService.setAiModel()`) |
| 5 | POST | `/api/ai-engine/gbm/train` | On "Train GBM" | Train the Gradient-Boosting model on the memory bank |

### Per-agent controls (in the ranking cards)

- **Enable/disable toggle** — disabled agents are excluded from the ensemble vote on the next decision (optimistic UI, reverts + shows an error banner on failure).
- **Weight input** — a number field with **no upper ceiling** (the old `0–2` slider cap was removed); type any value ≥ 0. Pinned weights show an orange badge.
- **Auto** button — clears the manual override so the agent reverts to its learned/default weight. Appears only when a weight is actually pinned.
- After any change the page re-reads `/api/ai-engine/models` so the displayed state matches the server (avoids the registry-vs-DB weight mismatch).

### Weight stores (important)

Two stores exist and are kept in sync at read time:
- **Registry** (Redis `ai_engine:model_registry`) — what this page sets and what the ensemble uses live. `weight: null` = no override (use learned/default).
- **DB** (`ai_engine_agent_weights`) — the learned weight. `/api/ai-engine/learning/summary` overlays the registry override onto the displayed weight and flags `weightPinned` / `weightLearned`.

### The `day_structure` agent

The ensemble is **12 agents** (was 11). The 12th, **Day Structure**
(`backend/app/agents/day_structure.py`), reads the full day's candles to judge where
price sits in the day's range and the risk/reward of a long entry. It also acts as an
explicit **entry-gate veto** in sessions: a high-confidence SELL near the day high
blocks new long entries regardless of the other agents. A new agent must be added to
six frontend surfaces (color/icon/description dicts on Dashboard, AIEngine,
PatternMemory `AGENT_META`, and `FloatingSystemStatus`) plus the registry/ensemble
defaults to be fully visible.

> **Deployment note:** the ensemble runs in **two** containers —
> `stock-prediction-backend` (HTTP) and `stock-prediction-session-runner`
> (advances sessions). A Python process loads agent code once at startup, so after
> changing the agent roster **both** must be restarted:
> `docker restart stock-prediction-backend stock-prediction-session-runner`.

---

## Floating System-Status Panel

**File:** `frontend/src/components/FloatingSystemStatus.tsx`

A draggable floating button (bottom-right) that expands into a live **Docker control
panel**, backed by the [`/api/system`](../api/system) routes.

- Lists every project container with a status dot, **CPU% and memory** per service, and aggregate **CPU / Mem totals**.
- **Logs icon** per service, colour-coded by recent-log severity — 🔴 error, 🟡 warning, 🟢 clean. Clicking opens the interactive log viewer in a new tab.
- Per-service **Restart / Stop / Start**, plus a header **Restart all** (skips the backend).
- Polls every 30s; the endpoint is stale-while-revalidate so updates are instant after the first load. The manual Refresh sends `?fresh=true`.

> **camelCase gotcha:** the axios interceptor camelCases all response keys, so backend `cpu_pct` / `mem_used_mb` / `log_severity` are read as `cpuPct` / `memUsedMb` / `logSeverity` in the component.
