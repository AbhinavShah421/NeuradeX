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
| `sentiment` | **LLM news sentiment** — reads the [sentiment-service](../microservices/sentiment-service.md) signal (Google-News + LLM); the only price-independent agent |
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

---

## AI loss-learning

The mechanisms above learn **quantitatively** — agent weights shift, the RL
policy updates, and the memory gate vetoes setups whose similar fingerprints
historically lost. The loss-learning layer adds the **explanatory** half: *why*
a specific trade lost, captured as reusable lessons that feed back into
decisions.

```
losing trade ─▶ LLM post-mortem (root cause + failure_mode + lesson)
            ─▶ trade_postmortems ─▶ aggregate recurring failure modes
            ─▶ ai_engine:active_lessons ─▶ prepended to the AI analysis prompt
```

1. **Pull losses** — `POST /api/ai-engine/loss-learning/run` reads recent losing
   closed trades (with their recorded agent signals + market context) from the
   [feedback-service](../microservices/feedback-trainer.md).
2. **Explain each** — the LLM returns `root_cause`, a reusable `failure_mode`
   (e.g. *"chased momentum into resistance"*), contributing `factors`, the
   `lesson`, and an `avoid_when` condition (rule-based fallback if the LLM is
   off). Stored in the `trade_postmortems` table.
3. **Aggregate** — recurring failure modes become ranked **lessons**
   (`GET /loss-learning/lessons`), cached to `ai_engine:active_lessons`.
4. **Apply** — those lessons are **prepended to the AI's `/analyze` prompt**, so
   the next decision weighs the mistakes already made and flags matching "avoid"
   conditions — complementing the quantitative memory veto.

Surfaced on the **Orders** page → **AI Loss Learning** panel (per-trade
post-mortems + the lessons applied to future decisions). It's an LLM-reasoned
knowledge base layered on the existing adaptive learning — not a separately
trained model.

| Method & path | Description |
|---|---|
| `POST /api/ai-engine/loss-learning/run` | Analyse new losing trades → store post-mortems + refresh lessons |
| `GET /api/ai-engine/loss-learning/postmortems` | Recent per-trade loss explanations |
| `GET /api/ai-engine/loss-learning/lessons` | Aggregated lessons (failure mode · occurrences · avg loss · avoid-when) |

## Learning curve & system-event overlay

The Dashboard **System Learning Curve** is the system's self-measurement over
time. Win-rate alone is misleading for an asymmetric-payoff strategy (small
losses, large wins), so `GET /api/ai-engine/learning-curve` returns three aligned
series — **cumulative win-rate**, a **trailing-window rolling win-rate** (the
recency-sensitive "is it learning lately?" signal), and the **equity curve**
(cumulative return, which rises even when win-rate is below 50%) — plus a
**per-source breakdown with expectancy** so you can see that win-rate ≠
profitability. A `source` filter (PAPER/REPLAY/LIVE/BACKTEST) keeps the large
historical-replay backlog from drowning real paper/live trades.

Curve moves are correlated with platform changes via a **system-events overlay**:

| Method & path | Description |
|---|---|
| `GET /api/ai-engine/learning-curve?source=&window=` | Cumulative + rolling win-rate, equity curve, per-source expectancy, events |
| `GET /api/ai-engine/learning-events` | System-update markers shown on the curve |
| `POST /api/ai-engine/learning-events` | Log a change (`title`, `category`, `detail`, optional ISO `occurred_at`) |

Timing/config changes (autopilot entry-timing, paper-trading window) self-annotate
the curve, so a regime shift can be read against what changed.

## Calibrated entry band (confidence ceiling)

Post-trade analysis of 7k+ intraday trades found ensemble **confidence is
anti-predictive above ~0.70**: win-rate falls from ~40% in the 0.50–0.60 band to
**16% above 0.90**, and per-trade expectancy goes negative past 0.70 (the most
"confident" entries chase momentum that reverses intraday). The disciplined trade
gates (**Strict**, **Gentle**) therefore enforce a confidence **band** — both a
floor *and* a ceiling (`max_conf` ≈ 0.72) — skipping over-confident setups.
Restricting historical entries to the band lifts REPLAY expectancy from
≈ −0.10%/trade to ≈ +0.05%/trade. **Loose** stays uncapped by design.

## Scan accuracy — learning from prediction vs. actual

Every scan is graded against what actually happened, so the system measures and
improves its hit-rate day by day:

- **Intraday** — the morning watchlist is graded after the close on the realised
  same-day move (`evaluate_day`).
- **Delivery** — delivery picks are graded on their **N-trading-day forward
  return** (`SCAN_DELIVERY_HORIZON`, default 5; "correct" if it gained
  `SCAN_DELIVERY_TARGET_PCT`). The scheduler retries until the horizon elapses.

Both feed `POST /scan-feedback` tagged with `trade_kind`, persisted in
`scan_evaluations`. `GET /scan-evaluation` returns per-trade-day accuracy for
**both** kinds (`trend`, `delivery_trend`), each point flagged `meets_target`
against `SCAN_ACCURACY_TARGET` (default 0.55). When a day misses the target the
scanner **dampens its conviction multipliers** (EMA calibration) so the next
scans promote fewer high-grade picks until accuracy recovers — surfaced on the
Dashboard **AI Scan Accuracy** card with an under-target warning.

## High-conviction tier — precision over coverage

Directional accuracy on *every* pick can't reach 90% — markets are near-random at
the single-pick level, and the broad scan sits ~50%. The way to a high hit-rate
is **selectivity + abstention**: only *commit* to a pick when many independent
signals agree, and ignore the rest.

A pick is **committed** only when it is a grade-A BUY with all six confirmations
(trend, momentum, MACD, volume, regime, RSI), a win-probability above an adaptive
floor (`ai_engine:hc_params`), **and** the genuinely-independent signals agree:
a confirmed higher-timeframe uptrend, no negative news catalyst, and **the learned
Pattern Recognition Model's P(up) ≥ `SCAN_PATTERN_MIN_P`** (0.55). The scanner pulls
the model's weights once per sweep (`/pattern-model/weights`) and scores each
candidate's pattern locally, so this gate adds no per-stock latency. Stacking
uncorrelated signals is what actually lifts the committed tier's accuracy — in
practice only a tiny fraction of candidates clear the pattern gate. Everything
else is "watch, don't trade". The committed tier is graded separately (`trade_kind='committed'`) and is
the only series measured against `SCAN_ACCURACY_TARGET` (0.90).

An **adaptive controller** (`_tune_hc_params`) tightens the bar each session when
the committed tier misses target (higher win-prob floor / more confirmations →
fewer, higher-confluence picks) and eases only if nothing qualifies. In backtest
the loose bar gave ~46% on ~32 picks/day; the strict bar gives **~71% on ~6
picks/day** with strongly positive average returns, and ultra-confluent days hit
90–100% on 1–7 picks. The realistic ceiling is precision/coverage-bound, so the
honest success metric is committed-tier **expectancy**, not raw hit-rate.

Shown as the purple **High-conviction** line on the Dashboard AI Scan Accuracy
card; `POST /api/ai-engine/backfill-committed` reconstructs its history.

## Pattern Recognition Model — backtest as a pattern trainer

A dedicated, continuously-learning model that considers **patterns only** — the
19-dim scale-free fingerprint of a candle window (see fingerprint.py), with no
indicators/news/RL. It is an **online logistic-regression** learner: every
labelled example nudges the weights, so it keeps getting smarter as backtesting
feeds it more `(pattern → realised forward move)` pairs. Weights, sample count
and a learning curve are persisted in Postgres (`pattern_model_state`,
`pattern_model_curve`) so progress survives restarts and is visualised on the
Dashboard **Pattern Recognition Model** card.

Training is pure pattern→outcome: `train_pattern_model` walks historical candles,
builds the fingerprint of each window (no lookahead) and labels it by the
realised return `horizon` bars later. The **backtest autopilot drives it** — each
completed backtest day kicks a (debounced) retrain, so backtesting's job becomes
making the recogniser smarter rather than only generating trades.

| Method & path | Description |
|---|---|
| `POST /api/ai-engine/pattern-model/train` | Train on patterns from backtest history (`lookback_days`, `horizon`, `stride`) |
| `GET /api/ai-engine/pattern-model/status` | Sample count + lifetime/recent accuracy + last-train summary |
| `GET /api/ai-engine/pattern-model/curve` | Accuracy as it has learned (the "getting smarter" chart) |
| `POST /api/ai-engine/pattern-model/predict` | P(up) for a candle window's pattern alone |

This is intentionally separate from the Pattern Memory k-NN bank: the memory bank
recalls specific past cases; this model generalises a smooth decision surface over
pattern space and reports a single, improving accuracy.

**Full universe + growth.** Training is not limited to the curated list — it reads
the scanner's full NSE universe (~2100) and trains a rotating `max_symbols` slice
each run (persisted `ai_engine:pattern_train_offset`), so coverage and the learned
sample count keep growing over time (e.g. 520k+ patterns and climbing).

**High-confidence accuracy (the path toward a high hit-rate).** Patterns alone give
a modest overall edge (~55%), and more data stabilises that — it does not make raw
directional prediction 90%. The honest lever is **selectivity**: the model reports
`high_conf_accuracy` — its accuracy on only the small subset where it is *sure*
(`|p-0.5| ≥ 0.30`, abstaining otherwise). That subset runs meaningfully higher
(~60%+) on a few % of patterns. Reaching 90% requires stacking this with the other
*independent* signals in the committed tier, not the pattern model alone.

## Scan-to-scan diff — why a rank moved

The scanner preserves the previous completed ranked board
(`ai_engine:ranked:prev`). `GET /scan-diff` compares it to the current board and
returns, per stock, the **rank delta**, names that **entered**/**dropped off**,
and a **reason** synthesised from the scoring components (call change, grade
change, win-probability shift, score delta, fresh news catalyst). Shown in the
Dashboard **AI Watchlist → "What changed since the last scan"** panel.
