---
sidebar_position: 6
title: Trading Controls
---

# Trading Controls

`/neuradex/controls` — the one page where live trading behaviour is changed.
Linked from the landing page and from Settings → Advanced.

Every knob that decides whether a trade happens used to live in one of three
places, none adjustable without a deploy: module constants read at import,
literals inside the `TRADE_GATES` presets, or environment variables. This page
puts a single Redis-backed override layer in front of all of them, so a value
can be changed and take effect on the next candle.

## What makes it safe to expose

**Bounds are server-side.** Every control declares a valid range and is rejected
outside it, in `services/controls.py` — not in the form. `score_min` is the sharp
edge: the gate compares a 0-100 entry score against it, so a typo of 7.8 for 78
does not tighten anything, it opens the floodgates on the next bar.

**Defaults travel with the control.** The shipped value is read *from the code
that owns it* rather than copied, so a default cannot silently drift out of sync
and `reset` always restores what actually ships. The one exception is the
scanner group — the scanner is a separate service whose values cannot be
imported, so they are mirrored with their source named in a comment.

**Evidence travels with the control.** Several of these have been measured and
the answer was "do not touch this". A page that lets you change a knob without
telling you it was already tested is worse than no page, so the measured result
is shown where the change is made.

## The faders

Numeric knobs are mixing-desk faders rather than number boxes, because the
question you actually ask here is *how far along its range is this, and how far
from what ships* — and a bare `0.68` answers neither. The lit track answers the
first; the tick under the cap answers the second.

**Values commit on release**, never during the drag. A write per input event
would send a live gate change for every pixel of a drag, at a session reading
these on the next candle. Release, Enter and blur commit; a pending row shows
amber with an `apply` fallback.

Rows light by meaning: accent normally, **red** for a knob carrying a documented
hazard, **amber** while a change is pending.

## Entry gate presets

The three presets — Strict, Gentle, Loose — are one tabbed bank rather than
three stacked ones. Two facts are shown at once because they are different
questions:

| signal | means |
|---|---|
| tab selection (accent) | which preset you are **looking at** |
| green pulsing lamp | which preset the runner is **reading** |

You can inspect Strict while Gentle trades. The tab opens on the live preset,
and **activating** one is a separate confirmed action — never a tab click, so
browsing the presets cannot change what trades.

`GET /api/ai-engine/controls` returns `active_gate`, read from the session
service's own Redis key rather than mirrored, so the page cannot drift from
what the runner actually uses.

## Control groups

| group | what it decides |
|---|---|
| Entry gate — Strict/Gentle/Loose | score floor, BUY voters, confidence band, override ceiling, co-signer requirement, pattern grade |
| Ensemble vote | vote mode, dominance margin, minimum voters a side needs |
| Pattern-memory gate | precedent cases required, veto and boost win rates |
| **Entry validator** | the anti-predictive confidence band |
| **Scanner — sectors** | minimum names to read a sector, breadth that counts as broad |
| **Scanner — movers** | gap that dominates a day, volume conviction and thin thresholds, RSI that marks a move late |

The scanner groups reach a *separate service*: the backend's control layer owns
the Redis key and the scanner reads it, applying overrides at the start of each
sweep. An untouched knob keeps the value in the worker's own code.

## Automation

Below the knobs, because the gates decide what a trade must look like and these
decide whether anything runs at all — you tune first and arm second.

- **Autopilot** — paper (live) and backtest replay, with entry timing and the
  backtest cursor.
- **Delivery autopilot** — multi-day paper portfolios whose exits an agent times.

Both moved off the Dashboard: they are switches, and a page of readings is a bad
place to keep controls that change live trading when mis-clicked.

## Related

- [Live sessions](./live-sessions.md) — where these gates are read
- [Learning loop](./learning-loop.md) — what the outcomes feed
