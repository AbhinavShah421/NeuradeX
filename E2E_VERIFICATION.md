# NeuradeX — End-to-End Flow Verification Checklist

One file, every user-facing flow, in the order you'd click through the app.
Use it to manually verify the whole product still works after a change —
tick boxes as you go, don't skip a section just because it "probably still
works."

**How to use this file**
- Work top to bottom on a fresh session; each section names its preconditions.
- `Verify via →` lines tell you where to look when the UI result is ambiguous
  (a Redis key, a log line, another page). Requires `docker exec` access to
  the compose stack.
- ⚠️ marks flows that touch **real money or real broker state** — don't run
  those against a live account unless you mean to.
- Container names below assume the `stock-prediction-` compose prefix seen in
  `docker ps`; ports match `docker-compose.yml`.

---

## 0. Environment sanity (do this first)

- [ ] `docker ps` — every `stock-prediction-*` container is `Up` (postgres /
      redis / rabbitmq / elasticsearch / influxdb / mongodb show `(healthy)`).
- [ ] Frontend loads at the configured URL (nginx origin) with no console
      errors; basename is `/neuradex`.
- [ ] `GET /health` on the backend returns DB + Redis connectivity OK.
      Verify via → `docker exec stock-prediction-backend curl -s localhost:8000/health`
- [ ] Floating system-status panel (bottom-right) expands and lists every
      container with a status dot, CPU%, and mem — not just placeholders.

---

## 1. Auth

### 1.1 Signup (3-step)
- [ ] `/signup` — Step 1: name, email, phone, password, confirm → submit →
      advances to OTP step without a page reload.
- [ ] Step 2: enter the 6-digit OTP received by email/WhatsApp → verified →
      advances to Step 3.
      Verify via → `docker exec stock-prediction-redis redis-cli get otp:<email>` if the message never arrives.
- [ ] Step 3: pick a broker (GROWW/ZERODHA/UPSTOX/ANGEL/NONE); if not NONE,
      enter api_key/api_secret → submit → redirected to `/` logged in.
- [ ] Wrong/expired OTP shows an inline error, not a silent failure.
- [ ] Duplicate email/phone shows a clear Pydantic-derived error message.

### 1.2 Login
- [ ] `/login` — email or phone + password → redirected to `/` (or the page
      you were bounced from).
- [ ] Wrong password → 401 → "Invalid credentials" inline, form stays filled.
- [ ] JWT persists across a hard refresh (`localStorage` key `neuradex-auth`).
- [ ] Expired/invalid token on any page → auto-redirect to `/login` (via
      `ProtectedRoute`'s `/api/auth/me` check).

### 1.3 Groww credentials (Settings-adjacent)
- [ ] `GET /api/auth/groww/status` reflects real token validity/expiry.
- [ ] `POST /api/auth/groww/refresh` forces a new OAuth token; status flips
      to a later `expires_at`.
      Verify via → memory note: Groww token needs **manual morning refresh** most days.
- [ ] `PUT /api/auth/groww/credentials` with a new key/secret updates without
      requiring logout.

---

## 2. Dashboard (`/`)

- [ ] Page loads: **AI Watchlist** tab active by default, stock rows show
      live price, change%, BUY/SELL/HOLD badge, confidence bar, target price.
- [ ] Model-accuracy banner at top shows a non-zero accuracy% once enough
      predictions exist.
- [ ] Click any watchlist row → navigates to `/stocks/:symbol`.
- [ ] **Watch button** on a row → button flips to "Watching" (amber),
      disabled while pending; re-scans promote it to "Promoted" if it
      re-scores grade A on the 2nd-level scan.
      Verify via → `docker exec stock-prediction-redis redis-cli get ai_engine:agrade_watch:manual:$(date +%F)`
- [ ] **Rescan** button (shared `ScanControl`) triggers a full sweep; button
      disables on *every* page while `scanning: true`, re-enables on finish.
      Verify via → `GET /api/ai-engine/scan-status` → `scanning` flips false.
- [ ] **System Learning Curve** card: three series render (cumulative
      win-rate, rolling win-rate, equity curve); source filter
      (Paper/Replay/Live) changes the curve; vertical event markers show a
      tooltip on hover.
- [ ] **Pattern Recognition Model** sparkline renders with a sample count and
      a working **Train now** button (kicks `/pattern-model/train`, sample
      count increases after).
- [ ] **AI Scan Accuracy** card: separate Intraday / Delivery / High-conviction
      lines plot against the target line; a day below target shows the
      under-target warning.
- [ ] **"What changed since the last scan"** panel lists rank movers with a
      reason string, entrants, and drop-offs after a rescan completes.
- [ ] **Autopilot** banner: Paper toggle ON starts sessions for untried
      watchlist symbols within one tick (`AUTOPILOT_TICK_SECS`, ~60s) during
      market hours; Backtest toggle behaves outside market hours only.
      Verify via → `docker exec stock-prediction-redis redis-cli smembers ai_engine:autopilot:started:$(date +%F)`
- [ ] **Reset to last trading day** (backtest cursor) moves the next-trade
      date back, stops any in-flight replay queue, and — if backtest is
      currently allowed — starts a fresh queue immediately.
- [ ] **Delivery Autopilot** card: toggle ON/OFF persists; **Run now** ticks
      immediately; **+ Portfolio** creates a new delivery paper portfolio
      with capital/target%/stop%/max-positions; delete removes it.
- [ ] **Tab 2: All Stocks Directory** — search box, sector dropdown, exchange
      toggle (All/NSE/BSE) each re-query and reset to page 1; column-header
      click toggles sort with a ⇅/▲/▼ indicator; price min/max and
      gainers/losers filters narrow the *current page* only (count reads
      "N of total"); **Clear** resets sort + filters.

---

## 3. Stock Detail (`/stocks/:symbol`)

- [ ] Navigating in loads: live quote/OHLCV, AI prediction (signal +
      confidence + target/stop/R:R), news sentiment score, last-20 prediction
      history — all four load without one blocking the others.
- [ ] Real-time tick updates the price/change without a manual refresh
      (Socket.IO `tick` event on the joined room).
- [ ] **Place Order** form: BUY/SELL, MARKET/LIMIT (price field appears only
      for LIMIT), quantity, CNC/MIS product → submit → confirmation +
      order_id returned.
      ⚠️ confirm this is hitting paper/backtest state, not a live Groww order,
      before running against a funded account.

---

## 4. Agent Detail (`/agents/:agent`, reached from an Orders trace popup)

- [ ] Loads every executed trade this agent voted on, with its vote, weight,
      confidence, ensemble action, and a correct/incorrect flag.
- [ ] Filter chip (BUY/SELL/HOLD/ALL) narrows the trade list; the summary
      accuracy numbers match the filtered `by_action` breakdown.

---

## 5. Portfolio (`/portfolio`)

Preconditions: a Groww account with live holdings (or accept an empty state).

- [ ] **Holdings** tab: symbol/qty/avg/CMP(live)/P&L/P&L% — CMP is a real
      live price (Yahoo fallback when Groww live-data isn't entitled), not a
      frozen average.
- [ ] **Performance** tab: Sharpe, max drawdown, returns render.
- [ ] **Risk** tab: HHI concentration, VaR, sector breakdown render.
- [ ] **AI Optimize** tab: per-holding signal + EXIT/TRIM/HOLD/ADD verdict +
      target weight; an at-risk holding shows an AI **alternative** and a
      one-click **Swap** (opens the confirm modal, shows the cost basis);
      "Today's Orders" panel lists live orders with a working **Cancel**.
      `refresh=true` (via the page's refresh action) recomputes rather than
      serving the scan-keyed cache.
- [ ] **AI Invest** tab: enter an amount → picks divide it (conviction-
      weighted, ≤35%/stock) as protective LIMIT buys; per-stock **Buy** and
      **Invest all** both open the confirm modal before placing anything.
- [ ] **AI Advisor** tab: alpha vs NIFTY over 1M/3M/1Y + an LLM insights feed
      (or its rule-based fallback if the LLM is down) render without error.
- [ ] **AI Risk Lab** tab: true diversification score + hidden-concentration
      pairs, a scenario stress-test (market/sector/rate shocks + fragile
      names), ATR smart-exit levels, dividend income forecast all populate.
- [ ] **Health Score** tab: 0–100 gauge + factor bars + an issues/fixes list.
- [ ] **Sector Exposure** tab: donut (current vs AI target), over/under bars,
      and concrete rebalance moves (TRIM/ADD with a stock suggestion).
- [ ] **AI Funds** tab: baskets (Top Picks / Sector Leaders / Momentum /
      Balanced / High-Conviction) list holdings+weights; **Invest** on a
      basket opens the confirm modal and sizes to real prices.
- [ ] **Goal Planner** tab: goal amount/years/risk/current corpus → required
      SIP or projected corpus + growth chart + allocation; **"Find my risk"**
      questionnaire updates the risk profile used.
- [ ] **Tax Harvest** tab: unrealised gains/losses, loss-harvest candidates,
      estimated tax saved render.
- [ ] Every order placed anywhere on this page requires the confirm modal —
      no one-click silent execution.
- [ ] Order book stays in sync with Groww on tab focus (refetch), not just on
      mount.

---

## 6. Predictions (`/predictions`)

- [ ] Filter bar Top 10/20/50/100/All changes the row count; header shows
      `scanned/universe · ranked · time`.
- [ ] Table columns populate: rank, symbol/company, action, grade, win%,
      signal score, momentum, price.
- [ ] Click a row → detail modal: rank/win-probability rationale, factor
      votes (trend/momentum/MACD/volume/regime/RSI as ✓/✗), LLM news
      sentiment, full indicator evidence, reasoning string.
- [ ] After a Rescan completes, the board auto-repulls and ranks change
      (don't need a manual page refresh).

---

## 7. Mutual Funds (`/mutual-funds`)

- [ ] **My Funds** tab: search-to-add finds a scheme by name, add with
      units+invested; table shows live NAV/current value/P&L and
      1M/3M/6M/1Y/3Y returns; **AI scan & replace** returns a HOLD/REVIEW/
      REPLACE verdict with a peer suggestion when REPLACE.
- [ ] **Optimize** tab: risk selector (conservative/moderate/aggressive)
      changes the asset-allocation bars and the action-plan cards
      (KEEP/REPLACE/CONSOLIDATE); an AI summary renders.
- [ ] **Screener** tab: category chips filter the leaderboard; Rank-by
      toggle (1Y Return / Risk-adjusted) resorts; ⭐ AI top-picks are
      visually distinct in both themes.
- [ ] Removing a fund from My Funds actually removes it (not just hides it
      client-side) — reload the page and confirm it's gone.

---

## 8. AI Engine section (`/ai-engine/*`)

### 8.1 Live Analysis (`/ai-engine`)
- [ ] Enter symbol + capital → **Start Session** → paper-trading session
      created.
- [ ] **Analyze** → ensemble vote returns final signal, confidence, per-agent
      votes+weights, reasoning.
- [ ] **Record Outcome** (PROFIT/LOSS/BREAKEVEN) → confirmation, and the
      outcome shows up in **Agent Performance** / **Prediction History**
      tabs shortly after.
- [ ] **Agent Performance** tab: per-agent weight and accuracy render and
      differ meaningfully between agents (not all identical placeholders).
- [ ] **Prediction History** tab: table of past predictions with outcomes.

### 8.2 AI Agents — Ollama LLM (`/ai-engine/agents`)
Preconditions: Ollama running and reachable from the backend container.
- [ ] Page mount loads a stock list and a model list (e.g. llama3.1:8b).
- [ ] Pick symbol + model → **Analyze** → natural-language analysis +
      indicators + recommendation + confidence render; loading steps show
      progressively (Fetching candles → Indicators → LLM → Formatting).
- [ ] Ollama unreachable → a clear error, not a hang or blank screen.

### 8.3 Backtest (`/ai-engine/backtest`)
- [ ] **Strategy Backtest** tab: pick symbol/strategy/date-range/capital/
      commission/params → **Run** → equity curve renders (Lightweight-charts,
      auto-aggregated timeframe), trades list + metrics (Sharpe, max
      drawdown, win-rate, total trades) populate.
- [ ] Changing a strategy's params (e.g. SMA fast/slow) actually changes the
      backtest result on rerun — not a cached identical result.
- [ ] Result + inputs persist across a page refresh (localStorage).
- [ ] **AI Live Trading** tab (= `SessionManager mode="replay"`): start a
      replay run (symbol, date or Auto = last trading day, start time,
      capital, speed 1×/2×/5×/10×, Normal/Aggressive timing) → session
      appears under **Running Sessions** within ~3s.
- [ ] Selecting a running session shows: live P&L, cash, position, unrealised
      P&L, a candlestick chart with BUY/SELL markers, the **Live AI
      Decision** panel (action, confidence, executed/no-trade, reason,
      timing signal, ensemble action, RSI/momentum/VWAP, per-agent votes),
      and an expandable **Decision log** (last ~30 candles).
- [ ] Speed buttons (1×/2×/5×/10×) on a running session actually change how
      fast candles advance.
- [ ] **Stop** halts advancement (status leaves `running`); **Remove**
      deletes it from the list.
- [ ] A finished/stopped session's trades show up on the **Orders** page,
      tagged `BACKTEST`/`REPLAY`.
- [ ] Refreshing the browser mid-session does not lose it — it's still
      running and reselecting shows current state (server-side session,
      not client state).
      Verify via → `docker exec stock-prediction-redis redis-cli smembers live_sessions:index`

### 8.4 Paper Trading (`/ai-engine/paper-trading`)
Same `SessionManager`, `mode="paper"` — only real market data, only during
NSE hours (09:15–15:30 IST).
- [ ] Start a session with a real symbol → appears in Running Sessions;
      candles advance roughly once a live minute closes (not every poll).
- [ ] Live AI Decision panel's `reason` string is legible and specific (not
      just "No entry") — e.g. names the RSI value, VWAP relation, and any
      veto by name (`ensemble veto honored — anomaly veto: ...`,
      `day-structure veto: ...`, `entry score N < threshold`).
      Verify via → memory note: anomaly-agent + scored-gate fixes (2026-07-14)
      changed exactly this reasoning; if you see every decision vetoed with
      "anomaly veto: abnormal price/volume" and `score` near 9–13, that bug
      has regressed — check `backend/app/agents/anomaly.py` trims trailing
      zero-volume bars before scoring.
- [ ] A BUY that actually executes shows up in the **Trades** table below
      the chart with price/qty/reason, and moves cash down / opens a LONG
      position in the stats row.
- [ ] A closed round-trip appears in **Orders** tagged `PAPER`, and the
      agents that voted on entry are visible in its execution trace.
- [ ] Outside market hours, no new paper sessions advance (existing ones
      stay parked, not erroring).
- [ ] Square-off at the configured cutoff force-closes any open LONG
      (check the `no_entry_after`/`squareoff_after` values on the
      **Paper-config** endpoint if unsure — `auto` resolves to the system's
      own learned times).

### 8.5 Agents & Memory (`/ai-engine/memory`)
- [ ] Page loads: pattern-memory bank totals (by action/source/top-symbols),
      per-agent accuracy + effective-weight ranking, all **12 agents**
      listed (technical, pattern, momentum, volatility, sentiment, rl,
      memory, meanrev, regime, anomaly, gbm, day_structure).
- [ ] Toggling an agent's enable switch off excludes it from the next
      ensemble decision (optimistic UI flips immediately; on failure it
      reverts + shows an error banner).
- [ ] Typing a weight value (no upper cap) and it saves; **Auto** button
      appears once pinned and clears the override back to learned/default.
- [ ] After any change, the page re-reads `/api/ai-engine/models` so the
      displayed value matches the server (no stale optimistic state).
- [ ] **Train GBM** button kicks a training run and its status/metrics
      update afterward.
- [ ] **Trigger nightly sweep** (manual) rebuilds the BACKTEST portion of the
      memory bank without touching LIVE/PAPER/REPLAY cases (compare
      `memory/stats` totals by source before/after).
- [ ] After changing which agents are enabled, confirm the change is live in
      **both** places — a Live Analysis run and a running paper session
      should both reflect it. If not, the agent roster needs both
      `stock-prediction-backend` and `stock-prediction-session-runner`
      restarted (Python only loads the agent module list at startup).

### 8.6 Live Trading (`/ai-engine/live-trading`) — ⚠️ REAL MONEY
This is **not** a simulated session — `live_trading.py` places real Groww
**MARKET MIS** orders when enabled with auto-execute on.
- [ ] Status loads: enabled/auto-execute flags, current settings
      (conviction_min, agreement_min, max_capital_pct, max_positions,
      allocated_capital), open positions, today's history, realised P&L,
      minutes to square-off, market-open flag.
- [ ] Enabling **without** auto-execute surfaces evaluation
      (gate-passed/reason/action/confidence/agent-agreement/recommended
      qty) without placing any order — confirm no order appears in Groww's
      own order book.
- [ ] The conviction gate actually blocks: confidence < 0.72 or agent
      agreement < 0.55 or action == HOLD → `gatePassed: false` with a
      specific `reason`.
- [ ] ⚠️ Only enable auto-execute against a live account intentionally. If
      you do: confirm a position appears in **both** this page and the
      Groww app/website, and that it auto-squares-off at 15:10 IST.
- [ ] Manual **square off** button closes a position immediately and it
      moves from Positions into today's History with a realised P&L.
- [ ] Disabling the engine stops new auto-executions but does not touch
      already-open positions.

---

## 9. Orders (`/orders`)

Preconditions: at least one closed trade in any mode.
- [ ] Loads aggregate stats (total trades, win rate, total P&L, avg
      return/trade, Sharpe) from the **feedback-service** (port 8012), and
      the full trade list.
- [ ] Mode filter ALL/LIVE/PAPER/BACKTEST actually filters the list.
- [ ] Click a trade → **Execution Trace** modal shows all 6 steps (Market
      Data Fetch → Technical Analysis → Sentiment → Ensemble Vote → Risk
      Validation → Order Execution) with real values, not placeholders.
- [ ] Click an agent name inside the trace → navigates to `/agents/:agent`
      pre-filtered to trades that agent voted on.
- [ ] **AI Loss Learning** panel: recent losing trades show a root cause,
      failure mode, and lesson; aggregated lessons list (failure mode ·
      occurrences · avg loss · avoid-when) is populated once enough losses
      exist.
      Verify via → `POST /api/ai-engine/loss-learning/run` then
      `GET /api/ai-engine/loss-learning/lessons`.
- [ ] Every trade opened from a chart shows BUY/SELL entry/exit markers on
      the correct candles.

---

## 10. Model Registry (`/models`)

Preconditions: MLflow reachable, at least one registered model.
- [ ] Loads registered models list via the backend's MLflow proxy (not a
      direct browser call to MLflow).
- [ ] Per-model metrics (accuracy/Sharpe/max drawdown depending on model
      type) render from that model's latest run.
- [ ] A model with no runs yet shows an empty/placeholder state, not an
      error that breaks the whole page.

---

## 11. Recordings (`/ai-engine/recordings`)

- [ ] Pick symbols via the multi-stock picker + a name → **Create** → new
      recording appears with status `scheduled`.
- [ ] Once its window starts, status flips to `recording` (red dot); once
      done, `completed` (green check).
- [ ] Expanding a completed recording shows per-symbol coverage: ticks,
      first/last time, full-day flag, start/end-clean flags, and a coverage
      summary (symbols / symbolsWithData / fullDay / totalTicks).
- [ ] A recording that never got real data during its window shows an
      honest zero-coverage state rather than fabricated numbers.
- [ ] Delete requires the in-app two-step confirmation (not a native
      `confirm()` dialog — those are silently suppressed in some mobile
      webviews) and actually removes it from the list after confirming.

---

## 12. Settings (`/settings`)

- [ ] Loads current data-provider settings: per-provider (Groww/Yahoo/Alpha
      Vantage) availability, enabled flag, whether a key is configured.
- [ ] Switching **Primary source** (Auto/Groww/Yahoo) saves and is reflected
      on reload (not just in local state).
- [ ] Entering/saving an Alpha Vantage key persists and `hasKey` flips true.
- [ ] With Primary forced to Yahoo, confirm a symbol's Stock Detail page
      still loads quotes (proves the fallback path actually works, not just
      that the toggle saves).

---

## 13. Autopilot & Watchlist deep checks (cross-cutting)

These are already touched from the Dashboard above; verify the underlying
mechanics directly when the Dashboard state is ambiguous.

- [ ] `GET /api/ai-engine/watchlist` returns non-empty `items` (intraday)
      during/near market hours, and `delivery` picks with an estimated safe
      holding window.
- [ ] `GET /api/ai-engine/autopilot` — `paper.enabled`, `paper.running`,
      `paper.watchlistSize`, `backtest.enabled`, `backtest.activeWindow`,
      `backtest.cursorDay`, `backtest.daysTrained` all populate sensibly
      (not all-zero/null).
- [ ] Backtest mode never runs while a paper session is live, and yields
      exactly at 09:00 IST / resumes at 15:40 IST on weekdays (freely on
      weekends) — spot-check the cursor doesn't advance during market hours.
- [ ] Post-market (~15:40 IST): `GET /api/ai-engine/scan-evaluation` returns
      a fresh grade for that day; the Dashboard's "Last signal score" panel
      matches it.
- [ ] A-grade live watcher: a manually-Watched or auto-flagged symbol that
      re-scores grade A on the second-level scan actually gets promoted into
      a live paper session (state flips Watching → Promoted in the UI).
      Verify via → `docker exec stock-prediction-redis redis-cli get ai_engine:agrade_watch:manual:$(date +%F)`
      and confirm a matching session id in `live_sessions:index`.
- [ ] At market close, the watcher's per-day state clears (doesn't silently
      carry stale "Watching" symbols into the next day).

---

## 14. Microservice-level checks (skip the UI, hit the service directly)

Only needed when a UI flow above looks wrong and you need to localize which
service is at fault. All ports are on `localhost` from the host, or the
container's own hostname from inside the compose network.

| Service | Port | Quick check |
|---|---|---|
| Backend (FastAPI) | 8000 | `GET /health` → DB + Redis OK |
| Technical agent | 8002 | publishes to `agent.signals` on `market.data.technical` |
| Sentiment agent | 8003 | reads `ai_engine:sentiment:{SYMBOL}` (written by sentiment-service) |
| Macro agent | 8004 | signal cached in Redis, TTL `MACRO_REFRESH_SECONDS` |
| Pattern agent | 8005 | candlestick pattern signals from `ohlcv` |
| RL agent | 8006 | consumes `market.data.rl` + `trade.outcomes.rl` |
| Ensemble engine (microservice) | 8007 | `GET /decision/{symbol}` — reads `ensemble:{SYMBOL}` (300s TTL). **Note:** distinct from the in-process ensemble that powers Live Analysis/sessions — see memory note "Two ensemble pipelines." |
| Risk engine (Java) | 8010 | consumes `ensemble.decision`, publishes `risk.validated` |
| Trade executor (Java) | 8011 | `PAPER_TRADING_MODE=true` sim-fills; `false` calls Groww |
| Feedback service | 8012 | `GET /stats`, `GET /trades` — same data Orders page reads |
| Model trainer | 8013 | consumes `model.retrain`; retrains RL policy + technical/pattern models every `RETRAIN_SCHEDULE_HOURS` |
| Stock scanner | 8014 | `GET /health`, `GET /status`, `POST /scan`, `GET /evaluation` |
| Autopilot service | 8015 | owns the paper/backtest training loops; backend only proxies |
| Sentiment service | 8016 | `GET /health`, `POST /refresh`, `GET /sentiment/{symbol}` |
| MLflow | 5000 | proxied via backend `/api/mlflow/*` |

- [ ] `docker restart stock-prediction-backend stock-prediction-session-runner`
      after any agent-roster change — confirm new/removed agents show up in
      **both** Live Analysis and a running paper session (Python loads the
      agent module list once at startup in each container).

---

## 15. Regression watch-list (known-fragile spots)

Cross-reference against the project memory before signing off a release —
these have broken silently before:

- [ ] Recreating any container doesn't leave nginx pointing at a stale
      upstream (empty dashboard after a restart ≠ a data bug — check nginx
      first).
- [ ] Frontend numeric fields aren't reading raw snake_case keys directly
      (axios camelCases everything; a NaN/blank field is usually this, not
      missing data).
- [ ] The **microservice** ensemble-engine (Orders' execution trace) and the
      **in-process** backend ensemble (Live Analysis / sessions) are not
      accidentally conflated when debugging a "wrong decision."
- [ ] A global CSS/theme change actually reaches components that are
      inline-styled (most of the app is) — spot check a couple of pages in
      both themes, don't assume a `.nd-*` class change is enough.
- [ ] The anomaly agent isn't scoring an unfilled (zero-volume) live candle
      as an outlier again (see §8.4) — this alone can silently zero out
      trading for days.
- [ ] The scored entry gate's hard stops (falling knife, RSI<45,
      trusted-expert dissent, ensemble/anomaly veto, throttle, cooldown,
      day-structure veto) are still present — a refactor that turns these
      back into soft points would let losing setups back in.

---

*Last built from a manual audit of the frontend routes, backend API routers,
and docs-site on 2026-07-14. Route/endpoint names change — if a step
references something that no longer exists, that's itself a finding worth
noting, not a checklist bug.*
