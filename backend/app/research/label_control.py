"""Is the extension effect real, or is it the exit geometry?

The feature survey found that price extended above its intraday VWAP predicts a
worse simulated long. That was measured on `cf_pnl_pct`, which simulates
LIVE_POLICY — entry, ATR stop, target, trail, time exit. A stop placed a fixed
number of ATRs below an extended price sits mechanically closer to the recent
range than the same stop below a price on its mean, so the feature could be
scoring by moving the stop rather than by predicting anything.

This runs the same candidates against raw forward returns, which have no stop,
no target and no exit rule. An effect on both labels is about the market. An
effect only under the simulated policy is about the geometry.

Memory
------
One day at a time. Each day is loaded, folded to a single edge per candidate,
and discarded. Peak memory is O(days x candidates) — a few hundred floats —
rather than O(rows). The first version of this sweep held every row for the
whole period, needed ~3GB inside a 2GB container, and took the Docker engine
down with it on 2026-08-18.

Run::

    python -m app.research.label_control 2026-06-01
"""
from __future__ import annotations

import asyncio
import resource
from collections import defaultdict
from datetime import date
from typing import Sequence

from app.research.feature_survey import (
    DERIVED_INDICATORS,
    derive_indicators,
    indicator_observations,
    _as_json,
)
from app.research.forward_returns import HORIZONS, PricePanel
from app.research.validation import Verdict, daily_edges, evaluate_from_daily_edges

LABELS = ("cf",) + tuple(f"fwd_{h}" for h in HORIZONS)

_DAYS_SQL = """
    SELECT DISTINCT created_at::date AS d
    FROM session_decisions
    WHERE cf_pnl_pct IS NOT NULL AND created_at::date >= :since
    ORDER BY 1
"""

_DAY_SQL = """
    SELECT symbol, candle_time, price, indicators, cf_pnl_pct
    FROM session_decisions
    WHERE cf_pnl_pct IS NOT NULL AND created_at::date = :day
"""


async def _load_day(db, day: date, *, matched: bool = False) -> list[dict]:
    """Load one day. `matched` keeps only rows where EVERY horizon resolves.

    Without it, comparing horizons compares different samples: a 240-minute
    forward return only exists for entries before ~11:30, so a long horizon
    silently becomes a statement about morning entries. Matching costs sample
    size and buys the right to read the horizon column as a trend.
    """
    from sqlalchemy import text

    raw = (await db.execute(text(_DAY_SQL), {"day": day})).fetchall()

    # The forward panel only needs this one day, so it is built and thrown away
    # with the day's rows.
    panel = PricePanel()
    for symbol, candle_time, price, _ind, _cf in raw:
        if price is not None and candle_time:
            panel.add(day, symbol, candle_time, float(price))

    rows: list[dict] = []
    for symbol, candle_time, price, indicators, cf in raw:
        r = {
            "d": day,
            "cf": float(cf),
            "_feat": derive_indicators(
                float(price) if price is not None else None, _as_json(indicators)
            ),
        }
        for h in HORIZONS:
            r[f"fwd_{h}"] = panel.forward_return_pct(day, symbol, candle_time or "", h)
        if matched and any(r[f"fwd_{h}"] is None for h in HORIZONS):
            continue
        rows.append(r)
    return rows


async def sweep(since: date, features: Sequence[str] = tuple(DERIVED_INDICATORS),
                *, matched: bool = False) -> dict:
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal

    edges: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    counts: dict[tuple[str, str], int] = defaultdict(int)

    async with AsyncSessionLocal() as db:
        days = [r[0] for r in (await db.execute(text(_DAYS_SQL), {"since": since})).fetchall()]
        for day in days:
            rows = await _load_day(db, day, matched=matched)
            for feature in features:
                for label in LABELS:
                    obs = indicator_observations(rows, feature, label=label)
                    if not obs:
                        continue
                    day_edge = daily_edges(obs).get(day)
                    if day_edge is not None:
                        edges[(feature, label)][day] = day_edge
                        counts[(feature, label)] += sum(1 for o in obs if o.selected)
            del rows                      # the whole point: nothing accumulates

    return {
        key: evaluate_from_daily_edges(f"{key[0]} [{key[1]}]", by_day,
                                       n_selected=counts[key])
        for key, by_day in edges.items()
    }


def report(verdicts: dict[tuple[str, str], Verdict]) -> str:
    features = sorted({f for f, _ in verdicts})
    lines = [
        f"{'feature':<22}{'label':<9}{'days':>5}{'edge %/day':>12}{'t':>7}   verdict",
        "-" * 74,
    ]
    for f in features:
        for label in LABELS:
            v = verdicts.get((f, label))
            if not v:
                continue
            lines.append(
                f"{f:<22}{label:<9}{v.n_days:>5}{v.mean_daily_edge_pct:>+12.4f}"
                f"{v.t_stat:>+7.2f}   {v.status}"
            )
        cf = verdicts.get((f, "cf"))
        fwds = [verdicts[(f, l)] for l in LABELS[1:] if (f, l) in verdicts]
        if cf and fwds:
            same = sum(1 for v in fwds if (v.mean_daily_edge_pct > 0) == (cf.mean_daily_edge_pct > 0))
            strong = sum(1 for v in fwds if abs(v.t_stat) >= 2.0)
            lines.append(
                f"{'':22}-> raw forward returns agree on sign {same}/{len(fwds)}, "
                f"{strong}/{len(fwds)} at |t|>=2  "
                f"=> {'REAL (survives without the exit rules)' if same >= len(fwds) - 1 and strong else 'GEOMETRY-DEPENDENT'}"
            )
        lines.append("")
    return "\n".join(lines)


async def main(since: date) -> None:
    from app.database.postgres import init_postgres

    await init_postgres()
    for matched in (False, True):
        label = ("MATCHED SAMPLE — only rows where every horizon resolves"
                 if matched else
                 "ALL ROWS — each horizon uses whatever it can resolve")
        bar = "=" * 74
        print("\n" + bar + "\n" + label + "\n" + bar)
        print(report(await sweep(since, matched=matched)))
    mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"peak RSS {mb:.0f}MB")


if __name__ == "__main__":
    import sys
    asyncio.run(main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 6, 1)))
