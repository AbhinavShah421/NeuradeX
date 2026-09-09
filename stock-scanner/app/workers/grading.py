"""Grade promotions against what the names actually did.

The question this answers is not "did promoted names go up". On a day the market
rises, everything goes up, and a promoter that picks at random posts a fine win
rate. The question is whether promoted names beat **the same day's** other
candidates — which means every reading here is a LIFT against a control drawn
from that day, never a raw return.

**Why day-clustered, and why that is not optional.**

The feature audit on 2026-08-11 found that pooling per-trade observations
produced significant t-stats for features that had no effect: readings inside
one day are not independent, because they all share that day's market. Pooling
n=400 trades across 4 days and quoting sqrt(400) treats 4 pieces of information
as 400. So the unit of evidence here is the DAY: compute one lift per day, then
test the mean of those lifts across days. `n` in the output is the number of
days, and it is deliberately the first thing reported.

**The control is the rejected set and the field.**

Two controls, because they answer different questions:

  vs rejected — did the REVIEWER add anything? If promoted and rejected names
                perform the same, the review is theatre.
  vs field    — did the PROMOTER add anything? If promoted names match the
                day's average candidate, the nomination is noise.

A promoter can beat the field while the reviewer contributes nothing, and vice
versa. Reporting one number would hide which half is working.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Optional

# Below this many days nothing is called established, whatever the t-stat says.
# The audit that motivated this file needed ~75 full-breadth days and had 4;
# a two-sided t on a handful of days is a number, not evidence.
MIN_DAYS_FOR_A_CLAIM = 20

# Bonferroni-style bar. Several promoter variants will be tested against the
# same corpus, and a 1.96 threshold applied repeatedly finds an effect roughly
# every twentieth time by construction.
T_ESTABLISHED = 2.8


def day_lift(promoted: list[float], control: list[float]) -> Optional[float]:
    """One day's lift: promoted mean minus control mean, in return points.

    None when either side is empty — a day with no promotions carries no
    information about the promoter and must not be counted as a zero, which
    would drag the mean toward no-effect and inflate the day count at once.
    """
    if not promoted or not control:
        return None
    return statistics.fmean(promoted) - statistics.fmean(control)


def cluster_t(lifts: Iterable[float]) -> dict[str, Any]:
    """t-statistic across DAYS, with the sample size stated first.

    Returns `n_days`, `mean_lift`, `t`, `pct_days_positive` and a `verdict` that
    refuses to overclaim on a short sample.
    """
    vals = [float(v) for v in lifts if v is not None]
    n = len(vals)
    if n == 0:
        return {"n_days": 0, "mean_lift": None, "t": None,
                "pct_days_positive": None, "verdict": "no data"}
    mean = statistics.fmean(vals)
    pos = 100.0 * sum(1 for v in vals if v > 0) / n

    t = None
    if n >= 2:
        sd = statistics.stdev(vals)
        if sd > 0:
            t = mean / (sd / math.sqrt(n))

    if n < MIN_DAYS_FOR_A_CLAIM:
        verdict = f"insufficient sample ({n} day{'s' if n != 1 else ''} of {MIN_DAYS_FOR_A_CLAIM})"
    elif t is None:
        verdict = "no variance"
    elif t >= T_ESTABLISHED:
        verdict = "established positive"
    elif t <= -T_ESTABLISHED:
        verdict = "established negative"
    else:
        verdict = "no effect"

    return {
        "n_days": n,
        "mean_lift": round(mean, 4),
        "t": round(t, 2) if t is not None else None,
        "pct_days_positive": round(pos, 1),
        "verdict": verdict,
    }


def grade_one_day(accepted: list[dict], rejected: list[dict], field: list[dict],
                  returns: dict[str, float]) -> dict[str, Any]:
    """Score a single day's promotions against that day's own control sets.

    `returns` maps symbol → forward return %. Symbols absent from it are dropped
    rather than treated as zero — a name that could not be priced is missing
    data, and calling it a flat outcome invents a result.
    """
    def rets(rows: list[dict]) -> list[float]:
        out = []
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if sym in returns:
                out.append(float(returns[sym]))
        return out

    a, rj, f = rets(accepted), rets(rejected), rets(field)

    return {
        "accepted": {"n": len(a), "mean": round(statistics.fmean(a), 4) if a else None,
                     "win_pct": round(100.0 * sum(1 for v in a if v > 0) / len(a), 1) if a else None},
        "rejected": {"n": len(rj), "mean": round(statistics.fmean(rj), 4) if rj else None,
                     "win_pct": round(100.0 * sum(1 for v in rj if v > 0) / len(rj), 1) if rj else None},
        "field": {"n": len(f), "mean": round(statistics.fmean(f), 4) if f else None,
                  "win_pct": round(100.0 * sum(1 for v in f if v > 0) / len(f), 1) if f else None},
        # The two lifts that matter, computed on this day only.
        "lift_vs_field": day_lift(a, f),
        "lift_vs_rejected": day_lift(a, rj),
    }


def aggregate(days: list[dict]) -> dict[str, Any]:
    """Roll per-day gradings into the two clustered readings.

    Deliberately reports both, because the promoter and the reviewer can each be
    working or not working independently, and a single headline number would
    hide which.
    """
    vs_field = cluster_t(d.get("lift_vs_field") for d in days)
    vs_rejected = cluster_t(d.get("lift_vs_rejected") for d in days)

    graded_days = [d for d in days if d.get("lift_vs_field") is not None]
    total_promoted = sum(d["accepted"]["n"] for d in graded_days)

    return {
        # Sample size first: it is what decides whether anything below is worth
        # reading, and burying it under a t-stat is how a 4-day result gets
        # quoted as a finding.
        "days_graded": len(graded_days),
        "promotions_graded": total_promoted,
        "promoter": {
            "question": "do promoted names beat the same day's other candidates?",
            **vs_field,
        },
        "reviewer": {
            "question": "do accepted names beat the ones the reviewer rejected?",
            **vs_rejected,
        },
        "note": (
            f"Lift is in return points against a same-day control. n is DAYS, not "
            f"trades — readings inside one day share that day's market and are not "
            f"independent. Nothing is called established under "
            f"{MIN_DAYS_FOR_A_CLAIM} days or |t| < {T_ESTABLISHED}."
        ),
    }
