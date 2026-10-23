---
sidebar_position: 9
title: Position Lifecycle
---

# Position Lifecycle

How a trade the executor opens gets closed. Added 2026-09-09, because until then
it did not.

## The bug this exists to end

`PaperTradingService.execute` published an entry to `trade.outcomes` and
**nothing ever published a close**. The consequences compounded quietly:

- Every trade the Orders pipeline took stayed open forever. Five permanently-open
  rows sat on a day that had two real trades, and they accumulated daily.
- The Orders page rendered them as `₹0.00 · —`, so they read as *empty sessions*
  rather than *open positions*, and inflated the session count.
- The weight learner never saw a single outcome from this path. A close is the
  only message that carries `pnl_pct`, so this producer taught it nothing.
- With no record of what it held, the executor opened **MIDHANI three times
  inside two minutes**, twice at an identical price. Each `risk.validated`
  message looked like the first one.

## This is not a second exit engine

The `stop_loss` and `take_profit` are computed upstream by risk-engine and ride
**on the `RiskValidated` message**. The monitor enforces the levels it was
handed; it invents no trailing stop, no time-stop and no target of its own.
Those are strategy, they live in the session runner, and duplicating them here
would give the system two disagreeing opinions about when to get out.

The one exit it adds is a same-day square-off at **15:20 IST**, which the live
path's `product=MIS` makes mandatory anyway.

## The parts

| file | job |
|---|---|
| `dto/OpenPosition.java` | one open leg, and the P&L arithmetic |
| `service/OpenPositionStore.java` | what is held, and the atomic claim on a symbol |
| `service/PositionMonitor.java` | the scheduled sweep, the exit rules, the close |
| `service/PriceFeed.java` | prices for held symbols |
| `service/PositionRehydrator.java` | reload open positions at boot |
| `controller/PositionController.java` | `GET /positions`, manual close |

## A symbol maps to a list, not a position

Going forward the rule is one position per symbol — a second BUY on something
already held is skipped. But that cannot be the *storage* shape, because the
duplicates that already exist have to stay closable. The first rehydrate found
five open rows across two symbols, and a symbol-keyed map silently dropped three
of them, recreating exactly the permanently-open rows being fixed.

Refusing new duplicates and closing existing ones are different questions, so
they are different methods over one list.

## Prices it can act on

The monitor polls `POST /api/stocks/ltp`, which **omits** any symbol it cannot
genuinely price.

:::danger Not `/directory/prices`
That endpoint looks like the same thing and is not: when Groww does not answer it
substitutes a **simulated** price so the directory grid has something to render.
A placeholder is right for a table of names and catastrophic here — exiting a
real position at a random price is worse than not exiting at all.
:::

The price is the last **1-minute candle close**, walking backwards to the last
bar that actually traded (a zero-volume bar is not a price anyone transacted at —
the same forming-candle trap that had the anomaly agent vetoing half of all
decisions for two days). `/live-data/ltp` is not used: it 403s on this account
even with a fresh token, and the client reads a 403 as a revoked token, forcing a
re-auth and a 30-minute backoff on the token every service shares.

## Rules that are deliberate

**A tick spanning both levels resolves to the STOP.** Which came first is
unknowable from a 30-second snapshot, and assuming the good fill books wins that
may not have happened.

**An unpriceable symbol at square-off stays open**, loudly. Closing at a number
we cannot stand behind is worse than staying open;
`POST /positions/{symbol}/close?price=…` is the escape hatch, and it takes an
explicit price because the situation it exists for is the one where none can be
fetched.

**A missing price is not an unchanged price.** An empty feed means "do nothing"
this tick, never "no movement".

**A level of `0` disables that leg** rather than firing immediately — otherwise
every restored position with no stored stop would stop out at once.

## The closing message

Published with the **same `trade_id` as the entry**. feedback-service upserts
`ON CONFLICT (trade_id)`, so the same id completes the entry row; a fresh id
would store two half-trades.

```
exit_price, pnl, pnl_pct, outcome, timestamp_close, duration_minutes, exit_reason
```

:::caution `pnl_pct` is a fraction
Every other producer stores it that way — mean `|pnl_pct|` is 0.0062 across 194
paper trades — and `determine_outcome`'s WIN/LOSS threshold is `0.001`. Sending
a percentage would not fail anything; it would quietly feed the weight learner a
100× signal on every closed trade.
:::

`exit_price` / `outcome` / `timestamp_close` are boxed types, not primitives, so
Jackson omits them on an entry. A primitive would serialise an entry's
`exitPrice` as `0.0`, which feedback-service reads as *a close at zero*.

## Surviving a restart

The store is in memory, so `PositionRehydrator` reloads open executor rows from
`GET /trades/open` at boot. Without it a deploy during market hours orphans
everything held, which is the original bug all over again.

It is best-effort on purpose: feedback-service being down must not stop the
executor from starting. A missed rehydrate strands old rows (recoverable, and
logged as an error); a refusal to start drops every signal for the rest of the
session.

A restored position with no stored stop or target is **not given one** — zero
disables that leg and the square-off still applies. Inventing a level would be a
fabricated exit.

## Deploying

`trade-executor` has **no bind mount**:

```bash
docker compose build trade-executor && docker compose up -d trade-executor
```

Tests: `PositionMonitorTest` (14), `RiskValidatedConsumerTest` (8).

Containers run UTC — every clock decision in the monitor is made explicitly in
`Asia/Kolkata`.
