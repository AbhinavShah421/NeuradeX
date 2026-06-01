---
id: stock-scanner
title: Stock Scanner
sidebar_position: 6
---

# Stock Scanner — Port 8014

**Entry point:** [`stock-scanner/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/stock-scanner/app/main.py) ·
**Core:** [`stock-scanner/app/scanner.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/stock-scanner/app/scanner.py) ·
**Universe:** [`stock-scanner/app/universe.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/stock-scanner/app/universe.py)

An **independent microservice** that continuously sweeps the market for stocks
that are a good fit for **intraday trading**, ranks them, and maintains the live
**AI Watchlist** in Redis. It runs on its own clock — a fresh scan before the
open, periodic re-scans during the day, and a self-grading pass after the close
that feeds the system's learning.

```
~09:00 IST  pre-open scan ─▶ AI watchlist (Redis: ai_engine:watchlist)
 intraday   periodic re-scans (every SCAN_INTERVAL) + manual /scan
~15:40 IST  post-close grade ─▶ signal score ─▶ calibrates next day's confidence
```

The backend never scans in-process; it just reads the Redis key the scanner
maintains and serves it at `/api/ai-engine/watchlist`.

---

## The universe

A curated dict of **108 liquid NSE symbols → names** (`UNIVERSE` in
`universe.py`) — index heavyweights plus high-beta intraday favourites. The
scanner sweeps this list and filters it down to the names that actually clear
the intraday bar.

---

## Market indicators

For each symbol the scanner fetches ~140 days of daily candles (Yahoo Finance,
`{SYMBOL}.NS`) and computes every input that moves an intraday price:

| Indicator | Role |
|---|---|
| Avg daily volume | **Liquidity** gate (`SCAN_MIN_VOLUME`, default 300k) |
| ATR % | **Volatility** gate (`SCAN_MIN_ATR_PCT`, default 1.2%) |
| Price floor | Avoids illiquid penny stocks (`SCAN_MIN_PRICE`, default ₹30) |
| Relative volume | Today's volume vs 20-day average (accumulation/distribution) |
| RSI (14) | Oversold-bounce / overbought-fade |
| Momentum (10-bar) | Directional push |
| SMA 20 / 50 | Trend regime |
| MACD histogram | Trend confirmation |
| Opening gap % | Day-start bias |
| Position in 20-day range | Room to the high / support distance |
| NIFTY regime | Broader market bias (`^NSEI` SMA + momentum → bull/bear/neutral) |

### Intraday-fitness gate & ranking

A stock is **kept** only if it clears liquidity **and** volatility **and** the
price floor. Kept names get:

- a directional **action** (`BUY` / `SELL` / `HOLD`) from a weighted vote over
  the indicators above (plus a half-weight nudge from the market regime), and
- a **signal score (0–100)** = tradability (liquidity + volatility + relative
  volume) blended with directional conviction, scaled by the learned
  **calibration multiplier** (below).

The watchlist is ranked **BUY first, then by signal score**, and the top
`SCAN_TOP_N` (default 15) are published.

---

## Post-market signal score (self-grading)

At ~15:40 IST the scanner grades that morning's picks against the **actual day
move** (`evaluate_day()`):

- For each pick it compares the predicted action with the realised day return
  (`(close − open) / open`), producing a per-stock **correct/incorrect** and a
  **realised return in the predicted direction**.
- It aggregates into an **accuracy** and an average realised return — the
  *signal score* for the day.
- The grade is stored in Redis and **pushed to the backend** for persistence and
  display.

### Calibration — the learning loop

`evaluate_day()` EMA-blends each day's per-action accuracy into **confidence
multipliers** (`ai_engine:scan_calibration`, clamped 0.7–1.3). The next scans
multiply their confidence/signal-score by that multiplier, so the scanner
**trusts historically-accurate signals more and shaky ones less** over time.

```
morning picks ─▶ end-of-day grade ─▶ accuracy ─▶ calibration multiplier ─▶ sharper next scan
```

---

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness + current state (last scan, market regime, last grade) |
| `GET` | `/status` | Full scanner state |
| `POST` | `/scan` | Trigger an immediate full sweep (the **Rescan** button proxies here) |
| `POST` | `/evaluate` | Grade a day's picks now (`?date=YYYY-MM-DD`, defaults today) |
| `GET` | `/evaluation` | The latest post-market signal-score grade |

---

## Redis keys

| Key | Writer | Reader(s) | Purpose |
|---|---|---|---|
| `ai_engine:watchlist` | stock-scanner | backend `/watchlist`, autopilot | Live ranked AI watchlist |
| `ai_engine:watchlist:premarket:{date}` | stock-scanner | scanner (eval) | Morning snapshot graded after close |
| `ai_engine:scan_calibration` | stock-scanner | scanner | Learned per-action confidence multipliers |
| `ai_engine:scan_eval:latest` / `:{date}` | stock-scanner | backend `/scan-evaluation` | Post-market grade |

---

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `SCAN_INTERVAL` | 1200 | Seconds between intraday sweeps |
| `SCAN_MIN_VOLUME` | 300000 | Liquidity gate (avg daily volume) |
| `SCAN_MIN_ATR_PCT` | 1.2 | Volatility gate (ATR %) |
| `SCAN_MIN_PRICE` | 30 | Minimum price |
| `SCAN_TOP_N` | 15 | Watchlist size |
| `SCAN_PREMARKET_MIN` | 540 | Pre-open scan time (09:00 IST, minutes past midnight) |
| `SCAN_POSTMARKET_MIN` | 940 | Post-close grade time (15:40 IST) |
| `BACKEND_URL` | `http://backend:8000` | Where the post-market grade is pushed |

---

## How it feeds the rest of the system

```
stock-scanner ─▶ AI watchlist ─▶ autopilot paper-trades it ─▶ outcomes train agents
      ▲                                                                   │
      └────────────── post-market signal score calibrates confidence ◀────┘
```

See [Watchlist & Autopilot](../ai-engine/watchlist-autopilot.md) for how the
watchlist is traded, and [Learning & Pattern Memory](../ai-engine/learning-loop.md)
for how trade outcomes train the agents.
