---
sidebar_position: 8
title: Scanner Workers
---

# Scanner Workers

`stock-scanner/app/workers/` — the scanner's analyses, split into focused
workers and composed by `_run_workers()` right after the movers board is
written.

## One sweep, many analyses

The obvious reading of "multiple scanners" is several processes each sweeping
the market. That would multiply a 2,298-symbol fetch by the worker count,
against a data source that already returns 403s under load, to compute things
that are all derivable from the per-symbol record the sweep already builds.

So the sweep stays single and each worker is a **pure function over its output**
— same inputs, independent questions, no extra API cost, and each one testable
on a list of dicts.

Every worker takes the `movers` list: one entry per analysed symbol carrying
`price`, `change_pct`, `gap_pct`, `rel_volume`, `rsi`, `atr_pct`, `uptrend`,
plus what the setup scorer concluded (`grade`, `action`, `signal_score`).

## sectors.py — what is actually working today

Ranks on the **median weighted by breadth**, never the mean. One stock up 18%
inside a nine-name sector produces the same mean as all nine up 2%, and only the
second is a sector move worth trading. A sector needs ≥ 4 analysed names before
it is ranked at all; smaller ones are reported but never ranked, because below
that a single stock *is* the sector.

The symbol→industry map comes from the backend's NSE-derived Redis cache
(`ai_engine:sector_map:<date>`). The scanner does not rebuild it — one owner,
one daily fetch. When the key is cold every symbol reads "Other" and the worker
degrades to a single bucket rather than failing; a scanner that stops sweeping
because a label lookup is cold would be much worse than an unlabelled sweep.

Output: `ai_engine:sectors` — hot, cold, per-sector detail, and a market breadth
reading computed over **every** analysed name (sector aggregation drops what the
map cannot label, and the market reading must not inherit that gap).

## movers.py — the day's moves, and why

Ranks purely on the day's move. This board answers *what moved*; the setup
scorer's board already exists and the two routinely disagree, which is
informative rather than a bug.

The value is the attribution. `TATASTEEL +6.2%` tells you nothing actionable.
These are different situations wearing the same number:

- *gapped +5.8% at the open — 94% of the move was priced before the bell, on 1.1× volume*
- *opened +0.2% and ground up all session on 3.4× volume, sector +2.1% on 78% breadth*

Every driver is read off figures the sweep already computed. Nothing infers
intent and nothing calls a model: **a reason that cannot be recomputed from the
record is a story, not a reason.**

Each move is classed `already happened` / `extended` / `in progress` / `thin`.
Only **in progress** gainers are eligible for promotion.

Output: `ai_engine:movers:attributed`.

## promotion.py — nominate, then review

A worker that spots something does not get to put it in front of the trader on
its own. It files a `Promotion` carrying every figure its claim rests on, and a
reviewer checks it.

The reviewer checks **facts, not plausibility** — same reasoning as the
[entry validator](../ai-engine/entry-validator.md). It rejects:

| rejection | why |
|---|---|
| any required parameter missing | a nomination that skipped a parameter has been guessed, not evaluated |
| gap-led finished move | promoting it offers the part that is already over |
| thin participation | a move nobody is trading unwinds as easily as it formed |
| buying strength (`uptrend`) | the measured worst cell — 22.6% win vs 28.6% |
| an A grade as the whole case | A-grade promotions realised 26.1% against a stated 82-95% |

Every constraint is **overridable by name**, so a promoter that supplies the
missing case gets through. What is refused is a promotion that never considered
the point. Rejections are *returned*, not dropped — the point of a reviewer is
lost if the reason a name missed the watchlist is invisible.

:::caution RSI is not a strength proxy here
The first live run rejected 0 of 8, because every accepted promotion read RSI
54-68 while up 6-13% on the day: this RSI is computed on **daily** candles, so a
name can rip intraday and still look mid-range. The constraint now tests
`uptrend` (price > SMA20 and SMA20 ≥ SMA50), which is what the measured finding
actually means. RSI remains a separate, weaker "extension" signal.
:::

Output: `ai_engine:promotions`, plus a dated snapshot
`ai_engine:promotions:<date>` (30 days) carrying accepted, rejected **and the
field with prices** — grading a promoted set without the same day's control
answers nothing, and the field cannot be reconstructed later.

## grading.py — did any of it work

`POST /grade-promotions`, read back at `GET /promotion-grades`.

Measures **lift against a same-day control**, never raw return: on a day
everything rises 3%, a promoted set that rises 3% added nothing.

Two controls, reported separately, because the promoter and the reviewer can
each work or not independently:

- **promoter** — accepted vs the whole day's field. Did nominating add anything?
- **reviewer** — accepted vs what it rejected. Did reviewing add anything? If
  these match, the review is theatre and the output says so.

**`n` is days, not trades.** Readings inside one day share that day's market and
are not independent; pooling 400 trades across 4 days and quoting √400 treats
four pieces of information as four hundred. One lift per day, then t across days.

It refuses to overclaim: nothing is "established" under **20 days** whatever the
t, the bar is **|t| ≥ 2.8** rather than 1.96 (several promoter variants will be
tried against one corpus), a day with no promotions returns `None` rather than
0, and an unpriceable symbol is dropped rather than zeroed.

## Tuning

The thresholds are on the [Trading Controls](../ai-engine/trading-controls.md)
page under **Scanner — sectors** and **Scanner — movers**. The scanner reads the
backend's override key from Redis and applies them at the start of each sweep;
an untouched knob keeps the value in the worker's own code.

## Deploying

`stock-scanner` has **no bind mount** — edits need `docker compose build
stock-scanner && docker compose up -d stock-scanner`. A restart silently no-ops
the change.

Tests: `stock-scanner/tests/test_workers.py`, `test_grading.py`.
