---
id: learning-loop
title: Continuous Learning & Pattern Memory
sidebar_label: Learning & Pattern Memory
---

# Continuous Learning & Pattern Memory

The AI Engine is a **7-agent ensemble** that learns from every trade it observes.
Three things are trained on every realised outcome — agent **weights**, the **RL
Q-table**, and the **Pattern Memory** bank — so the more you backtest and
paper-trade, the more accurate future predictions become.

> **Where it lives:** `backend/app/agents/` (`ensemble.py`, `learning.py`,
> `memory.py`, `fingerprint.py`, `rl_agent.py`) and `backend/app/api/ai_engine.py`.

---

## The 7 agents

| Agent | Signal source |
|---|---|
| `technical` | RSI, MACD, VWAP, SMA crossovers |
| `pattern` | Candlestick / price-shape patterns |
| `momentum` | Multi-bar rate-of-change |
| `volatility` | ATR regime + a risk score (can force HOLD) |
| `sentiment` | Momentum-derived sentiment proxy |
| `rl` | Q-learning policy (108-state table in Redis) |
| `memory` | **Case-based reasoning** over historical outcomes |

The ensemble combines them with a **confidence-weighted vote**; the final action
then passes through the **memory evidence gate** (below).

---

## Pattern Memory bank

Every decision is turned into a **fingerprint** — a 19-dimension, scale-free
vector capturing the recent price *shape* plus indicator context (RSI, MACD,
VWAP distance, ATR%, Bollinger position, volume ratio, momentum, trend slope) and
a coarse **regime** label (`up_high`, `flat_low`, …). Similar setups land near
each other in vector space.

```
new candle ─▶ fingerprint ─▶ k-NN search in pattern_memory (cosine, Redis-cached)
                                   │
                  "47 similar cases, BUY won 81% (+0.9% avg)"  ──▶ act + boost
                  "only 2 similar cases / low win-rate"        ──▶ veto → HOLD
```

### The evidence gate

A BUY/SELL only fires when ≥ `MIN_SAMPLES` similar past cases won often enough;
otherwise the ensemble **abstains (HOLD)**. Strong precedent boosts confidence.
This selectivity is what lifts win-rate on *taken* trades — the system only acts
when memory supports it.

---

## How training happens

| Source | Feeds memory | Trains agent weights | Trains RL |
|---|---|---|---|
| AI Live Trading / Paper sessions | ✅ | ✅ | ✅ |
| Strategy backtests (background, capped) | ✅ | ✅ | ✅ |
| Live Analysis (`/analyze` → `/outcome`) | ✅ | ✅ | ✅ |
| Nightly memory sweep | ✅ (rebuild) | — | — |

On each entry the ensemble decision is stored as a **prediction** (with its
fingerprint + RL state); on exit the realised P&L is recorded as an **outcome**.
`learning.record_outcome()` then:

1. updates each agent's **weight** (reward + correctness),
2. trains the **RL Q-table** from the stored state/action/reward,
3. promotes the case into **Pattern Memory**.

### Nightly memory sweep

A background job (`app/agents/memory_sweep.py`) runs at **~02:00 IST** and
rebuilds the `BACKTEST` portion of the bank from fresh real backtests across the
watchlist — *replace, not append*, so it stays bounded; `LIVE`/`PAPER`/`REPLAY`
cases are preserved. Trigger manually from the **Pattern Memory** page.

---

## Data model (PostgreSQL / TimescaleDB)

| Table | Purpose |
|---|---|
| `ai_engine_predictions` | One row per decision: action, confidence, agent signals, `rl_state`, `fingerprint`, context |
| `ai_engine_outcomes` | Realised P&L + reward per prediction |
| `ai_engine_agent_weights` | Per-agent weight, total/correct predictions, total reward |
| `pattern_memory` | Fingerprint cases: action, pnl%, regime, source (`BACKTEST`/`PAPER`/`REPLAY`/`LIVE`) |

---

## API

| Method & path | Description |
|---|---|
| `POST /api/ai-engine/analyze` | Run the 7-agent ensemble; stores a prediction |
| `POST /api/ai-engine/outcome` | Record a trade outcome → trains weights + RL + memory |
| `GET /api/ai-engine/performance` | Per-agent weight + accuracy |
| `GET /api/ai-engine/learning-summary` | Totals, overall accuracy, per-agent stats, 24h activity, memory size |
| `GET /api/ai-engine/memory/stats` | Memory size + win-rate by source/action |
| `POST /api/ai-engine/memory/query` | What memory recalls for a given candle window |
| `POST /api/ai-engine/memory/seed` | Bulk-seed memory from historical replays |
| `POST /api/ai-engine/memory/sweep` | Manually trigger the nightly rebuild |

The **Pattern Memory** page (AI Engine → Pattern Memory) shows the live
**Agent Learning** panel: predictions made, outcomes learned, overall accuracy,
per-agent weights, memory size, and a *"trained in last 24h"* indicator.
