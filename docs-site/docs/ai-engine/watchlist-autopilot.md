---
id: watchlist-autopilot
title: AI Watchlist & Autopilot
sidebar_label: Watchlist & Autopilot
---

# AI Watchlist & Autopilot

The **AI Watchlist** is the list of intraday-tradable stocks the system is
watching right now; **Autopilot** is the agent that paper-trades that watchlist
automatically so the system keeps learning. Together they close the
self-improving loop:

```
stock-scanner ─▶ AI watchlist ─▶ autopilot paper-trades it all ─▶ outcomes train agents ─▶ better next scan
        ▲                                                                                        │
        └───────────────────── post-market signal score calibrates confidence ◀─────────────────┘
```

> **Where it lives:** Autopilot is its own microservice —
> [`autopilot-service/`](https://github.com/AbhinavShah421/NeuradeX/blob/main/autopilot-service/) (port 8015) —
> which owns the training loops and starts sessions via the backend's sessions
> API. The backend only reads/writes the enable flags and proxies status. The
> watchlist comes from the [stock-scanner](../microservices/stock-scanner.md).

---

## AI Watchlist

The watchlist is produced **entirely by the independent
[stock-scanner](../microservices/stock-scanner.md) service** — no hard-coded
lists. It sweeps 108 NSE names, keeps only the intraday-fit ones, ranks them
(BUY first, then signal score), and writes the result to Redis
(`ai_engine:watchlist`). The backend serves it read-only at
`GET /api/ai-engine/watchlist`.

Each item carries its full **evidence** — liquidity, relative volume, ATR%,
RSI, momentum, SMA trend, MACD, opening gap, range position, market regime, a
signal score and a plain-English reasoning string — surfaced in the dashboard's
**AI Watchlist** tab and its per-stock detail modal.

---

## Autopilot — two training modes

Autopilot has **two independently-toggled modes**. Either or both can be ON;
the service time-slices them so they never compete (see below). Both produce
ordinary server-side [sessions](./live-sessions.md) — they appear on the **Paper
Trading / Live Sessions** pages and can be opened as a live chart — and **every
closed trade trains the agents** (weights + RL + pattern memory).

### Paper mode (live)

During NSE market hours, each tick (`AUTOPILOT_TICK_SECS`, default 60s) opens a
**live paper session for every watchlist stock** not already traded today (up to
`AUTOPILOT_MAX_SESSIONS`, default 15 — the whole watchlist). Each runs on **real
market data** through the full **12-agent ensemble**.

### Backtest mode (1× replay, continuous)

Runs **outside** market hours to keep training on dense historical data. It opens
**1× replay sessions for the last trading day**; when that whole queue finishes
it steps **back to the previous trading day** and runs again — walking backward
through history. Days without real intraday data are skipped, and it wraps after
`AUTOPILOT_BACKTEST_MAX_DAYS_BACK` so it keeps cycling.

#### Resetting the next trade date

The dashboard's Autopilot card shows the **next trade date** (the cursor the walk
will replay next) with a **Reset to last trading day** button. Resetting:

- moves the cursor to the **last trading day before the current date** (weekends
  skipped),
- **stops any in-flight replay queue** and clears the queue state,
- **preserves training history** (`completed_days` / `last_completed`) — only the
  walk position moves,
- if backtest is enabled and inside its allowed window, **immediately starts a
  fresh queue** for the new date.

Use it after a gap (e.g. the service was off for a few days) to re-anchor the walk
to the most recent session instead of resuming from a stale day.

### Time-coordination (backtest yields to paper)

So the agents focus entirely on live decisions during market hours, backtest is
allowed **only before 09:00 and after 15:40** on weekdays (freely on weekends).
At the 09:00 cutoff it **closes any running replay queue and starts nothing**;
it resumes after the close. A hard guard also yields if any paper session is
live. Net effect:

```
00:00 ── backtest training ──▶ 09:00 cutoff (close queues) ── 09:15 ─ PAPER TRADING ─ 15:30 close ── 15:40 ─▶ backtest resumes
```

### Rules / guardrails

| Rule | Behaviour |
|---|---|
| Gated | A mode runs only when its toggle is **ON** (paper also needs the market open) |
| Mutually exclusive | Backtest never runs during the paper window or while paper sessions are live |
| One per symbol per day | Paper tracks started symbols in Redis (`ai_engine:autopilot:started:{date}`), reset daily — no churn |
| Instant response | Toggling a mode on kicks the first queue immediately (no wait for the loop) |
| Resettable walk | The backtest cursor can be reset to the last trading day before today; in-flight queues stop, training history is kept |
| Auto square-off | Sessions close themselves at end of day |

### Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `AUTOPILOT_MAX_SESSIONS` | 15 | Max concurrent paper sessions (covers the watchlist) |
| `AUTOPILOT_CAPITAL` | 50000 | Capital per session |
| `AUTOPILOT_TICK_SECS` | 60 | Paper check cadence |
| `AUTOPILOT_BACKTEST_SPEED` | 1 | Replay speed (1× = dense, real-like) |
| `AUTOPILOT_BACKTEST_MAX` | 15 | Replay sessions per day queue |
| `AUTOPILOT_BACKTEST_MAX_DAYS_BACK` | 30 | Walk back N trading days, then wrap |
| `AUTOPILOT_BACKTEST_MORNING_CUTOFF` | 540 | 09:00 IST — stop backtest before market |
| `AUTOPILOT_BACKTEST_EVENING_RESUME` | 940 | 15:40 IST — resume after paper closes |

---

## Post-market signal score

After the close the [stock-scanner](../microservices/stock-scanner.md) grades
the morning's watchlist against the actual day move and pushes the result to the
backend, which persists it to `scan_evaluations` and exposes:

- `GET /api/ai-engine/scan-evaluation` → the latest grade + the per-day
  **accuracy trend** + an overall summary.

The dashboard's **AI Watchlist** tab shows this as a collapsible *Last signal
score* panel (accuracy, per-stock hits, avg realised return), and the scanner
uses the same accuracy to **calibrate** its future confidence.

---

## API

| Method & path | Description |
|---|---|
| `GET /api/ai-engine/watchlist` | Live ranked AI watchlist (read from Redis) |
| `POST /api/ai-engine/watchlist/scan` | Proxy a manual full sweep to the scanner |
| `GET /api/ai-engine/autopilot` | Combined status: `paper` (enabled, market, running, watchlist size) + `backtest` (enabled, active window, cursor day, queue progress, days trained) |
| `POST /api/ai-engine/autopilot` | Enable / disable a mode (`{ "mode": "paper"\|"backtest", "enabled": true }`) — proxied to the autopilot-service |
| `POST /api/ai-engine/autopilot/reset-cursor` | Reset the backtest next trade date to the last trading day before today (stops the queue, keeps training history) — proxied to the autopilot-service |
| `GET /api/ai-engine/scan-evaluation` | Latest post-market signal score + accuracy trend |
| `POST /api/ai-engine/scan-feedback` | (Internal) scanner pushes its grade here for persistence |
| `GET /api/ai-engine/learning-curve` | Cumulative win-rate over the ordered trade history |

---

## Where it shows in the UI

| Dashboard element | Backed by |
|---|---|
| **Autopilot** banner (toggle + live status + backtest next-trade-date reset) | `/autopilot`, `/autopilot/reset-cursor` |
| **AI Watchlist** tab + evidence modal | `/watchlist` |
| **Last signal score** panel | `/scan-evaluation` |
| **System Learning Curve** | `/learning-curve` |
| **Paper Trading** page (running auto sessions) | `/api/sessions` |
