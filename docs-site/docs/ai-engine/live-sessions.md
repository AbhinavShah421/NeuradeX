---
id: live-sessions
title: Live Trading Sessions
sidebar_label: Live Sessions
---

# Live Trading Sessions

AI Live Trading (historical replay) and Paper Trading (live market) run as
**server-side, background-advancing sessions**. State lives in Redis and a
background loop advances every running session with the full 7-agent ensemble —
so a session **survives a page refresh, keeps running in the background, runs
alongside others, and can be reopened** as a live chart.

> **Where it lives:** `backend/app/api/sessions.py`,
> `backend/app/utils/session_store.py`; frontend
> `components/SessionLauncher.tsx`, `pages/LiveSessions.tsx`.

---

## Why server-side

Previously these flows were *client-driven* — the browser held cash/position/
trades and the server was stateless, so a refresh wiped everything. Moving the
session state and its advancement to the server fixed that and unlocked
background execution + multi-session concurrency.

```
Browser  ──start──▶  /api/sessions/start ──▶ Redis: live_session:{id}
                                              │
              background runner loop (every ~2s) advances each running session
                                              │
Browser  ──poll───▶  /api/sessions/{id}  ◀── live candles, trades, position, P&L
   (reconnects via a session id saved in localStorage → survives refresh)
```

---

## Modes

| Mode | Data | Advancement |
|---|---|---|
| `replay` | Real intraday candles for a **past** trading day | Steps `speed` candles per tick (1× / 2× / 5× / 10×) |
| `paper` | **Live** market data (real prices) | Advances per completed candle during NSE hours (09:15–15:30 IST) |

### Decision logic

Each candle, the **intraday rule signal** times entries/exits (RSI/VWAP/momentum
+ take-profit, stop-loss, end-of-day square-off) while the **7-agent ensemble +
memory gate** confirm or veto the trade and supply confidence. A BUY fires only
if the rule signal triggers *and* the ensemble isn't bearish; the ensemble can
also trigger an early exit.

Completed round-trips are saved to **Orders** (tagged `BACKTEST`/`PAPER`) and
**train the AI engine** (weights + RL + memory) — see
[Learning & Pattern Memory](./learning-loop).

---

## Sessions API

| Method & path | Description |
|---|---|
| `POST /api/sessions/start` | Create a session: `{ mode, symbol, date?, start_time, capital, speed }` |
| `GET /api/sessions` | List all sessions (summaries) |
| `GET /api/sessions/{id}` | Full state: candles, trades, position, metrics, agent decision |
| `POST /api/sessions/{id}/stop` | Stop a running session |
| `POST /api/sessions/{id}/speed` | Change replay speed |
| `DELETE /api/sessions/{id}` | Remove a session |

---

## Frontend

- **Live Sessions** page (`/neuradex/ai-engine/sessions`) — start runs, see all
  running sessions, open any as a live chart; finished sessions move to **Orders**.
- The **AI Live Trading** tab and **Paper Trading** page are powered by the same
  `SessionLauncher` component: it stores the session id in `localStorage` and
  reconnects on mount, so refreshing never wipes the session.
- **Strategy Backtest** persists its last result + inputs to `localStorage`.
- Every order in **Orders** can be opened to view a price chart with BUY/SELL
  entry/exit markers.
