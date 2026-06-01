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

> **Where it lives:** [`backend/app/agents/autopilot.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/autopilot.py),
> [`backend/app/agents/market_scanner.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/market_scanner.py)
> (`get_watchlist()`), and the [stock-scanner](../microservices/stock-scanner.md)
> microservice that produces the watchlist.

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

## Autopilot

When **autopilot mode is ON**, a background loop
([`autopilot_loop`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/agents/autopilot.py))
runs every `AUTOPILOT_TICK_SECS` (default 60s). During NSE market hours each tick:

1. reads the live AI watchlist,
2. opens a **server-side paper-trading session for every watchlist stock** it
   hasn't already traded today (up to `AUTOPILOT_MAX_SESSIONS`, default 15 — the
   whole watchlist),
3. each session runs on **real market data** and is driven by the full **7-agent
   ensemble (+ pattern memory)**, and
4. every closed trade **trains the agents** (weights + RL + memory).

Those are ordinary [paper sessions](./live-sessions.md), so they appear on the
**Paper Trading** page and can be opened as a live chart like any other.

### Rules / guardrails

| Rule | Behaviour |
|---|---|
| Gated | Only runs when autopilot is **ON** *and* the market is **open** |
| One per symbol per day | Started symbols are tracked in Redis (`ai_engine:autopilot:started:{date}`) and reset daily — a finished session is never re-opened (no churn) |
| Auto square-off | Sessions close themselves at end of day |
| Capital | `AUTOPILOT_CAPITAL` per session (default ₹50,000) |

### Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `AUTOPILOT_MAX_SESSIONS` | 15 | Max concurrent auto paper sessions (covers the watchlist) |
| `AUTOPILOT_CAPITAL` | 50000 | Capital per session |
| `AUTOPILOT_TICK_SECS` | 60 | Seconds between autopilot checks |

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
| `GET /api/ai-engine/autopilot` | Autopilot status (enabled, market, watchlist size, started today, running sessions) |
| `POST /api/ai-engine/autopilot` | Enable / disable autopilot (`{ "enabled": true }`) |
| `GET /api/ai-engine/scan-evaluation` | Latest post-market signal score + accuracy trend |
| `POST /api/ai-engine/scan-feedback` | (Internal) scanner pushes its grade here for persistence |
| `GET /api/ai-engine/learning-curve` | Cumulative win-rate over the ordered trade history |

---

## Where it shows in the UI

| Dashboard element | Backed by |
|---|---|
| **Autopilot** banner (toggle + live status) | `/autopilot` |
| **AI Watchlist** tab + evidence modal | `/watchlist` |
| **Last signal score** panel | `/scan-evaluation` |
| **System Learning Curve** | `/learning-curve` |
| **Paper Trading** page (running auto sessions) | `/api/sessions` |
