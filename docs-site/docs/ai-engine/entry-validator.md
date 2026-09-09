---
sidebar_position: 7
title: Entry Validator
---

# Entry Validator

`backend/app/agents/validator.py` — the last checkpoint before a BUY becomes a
trade. It runs inside `sessions_service._step`, immediately before
`action = "BUY" if enter else "HOLD"`, **after** all nine existing strategy
vetoes.

## What it is, and deliberately is not

It is **not** a second opinion on whether the trade is a good idea. That was
tried here: the 8B reviewer's verdict tracked how the prompt was framed rather
than the precedent it was handed, which is why it has no vote in the ensemble.
A model that *judges* at this point would launder prompt sensitivity into an
execution decision.

So it verifies **facts**. Every check answers a question with a knowable answer.

## Two properties, both tested

**It can only block.** Nothing in it can turn a HOLD into a BUY. A bug in this
file can cost a trade; it can never cause one. `Verdict.ok` is derived purely
from the absence of reasons, so no code path can construct an approving verdict
that contained a failure.

**It fails closed.** A check that raises is treated as a block, not a pass — and
if the module cannot be imported, `_step` refuses the entry rather than trading
unchecked. It is the last thing between a decision and real money, so an
unusable validator must stop, not abstain.

## Checks

| check | blocks when |
|---|---|
| price | not finite, or ≤ 0 |
| bar | the decision candle has zero volume — an in-progress bar is not a tradable price |
| symbol | not in the tradable universe (renamed / delisted) |
| gate | the decision does not actually satisfy the gate it claims to have cleared |
| confidence | in the ≥ 0.90 anti-predictive band, unless `allow_anti_predictive` |
| position | already holding a LONG — refuses to stack a second entry |
| risk | daily loss limit reached, or position cap hit |
| market | closed (skipped for backtest/replay, which replay a finished day) |
| **recompute** | the score rebuilt from raw inputs disagrees with the claim |

`NaN` is checked explicitly because it is the value that slips furthest: it
compares false against every threshold, so a NaN price passes a `< limit` gate
silently.

## The recomputation

The other checks verify that the numbers handed in are self-consistent. This one
is stronger: it rebuilds the entry score from **raw inputs** — agent votes,
indicators, candle — and blocks on any disagreement.

`_step` accumulates `score` across ~120 lines and nine mutation points, then
gates on a conjunction of eight terms. A future edit that double-counts a
component, drops a branch or flips a comparison produces a score that is *wrong
but perfectly self-consistent*, and every consistency check would pass. Only a
second implementation reading the same inputs can catch that.

Weights reimplemented: consensus 30, reliable co-sign 10, trend 25, RSI 20+5,
ensemble stance 10, +5 timing bonus, capped at 100.

The scoring **constants** are imported rather than duplicated — copying
`_RELIABLE_BUY_AGENTS` would fire false alarms on every deliberate edit. The
**arithmetic** is what gets a second implementation, because that is where drift
happens.

:::note The trend filter has two branches
`NEURADEX_TREND_FILTER=legacy` reverses the polarity of the trend leg: the
default (post 2026-08-10) hard-blocks *chasing strength*, legacy hard-blocks the
*falling knife*. The recomputation honours whichever is live, and the tests pin
both rather than inheriting the ambient value.
:::

## Why it is separate from the veto chain

The nine `if enter:` blocks in `_step` encode opinions about which setups are
worth taking. This encodes invariants that hold whatever the strategy thinks —
and because it runs last, the gate-consistency check also catches the case where
one of those blocks is edited and drops a term.

## Built around two real failures

- **`entry_price` of 0.** Ninety trade records were stored with a zero entry
  price because a field name did not match across a service boundary. Nothing
  errored.
- **The forming candle.** A zero-volume in-progress bar had the anomaly agent
  vetoing roughly half of all decisions for two days.

Neither raised an exception at the time. Both are now checked.

The verdict is stashed on the session as `last_validation`. Tests:
`backend/tests/test_validator.py`.
