"""Prior-day and multi-day support/resistance levels, from daily bars.

Display only. These levels are computed at session start and returned in the
session detail payload for the chart to draw. They are deliberately NOT read by
`_step` or by any agent — see test_price_levels.py, which pins that boundary.

Why display-only matters here: the same battery of levels was measured as entry
signals on 2026-08-06 (app/research/level_report.py — pdh_break, pdl_bounce,
s1_bounce, below_s1, CPR, opening range, run-up families) and none was
profitable after costs. Drawing them for a human to read makes no edge claim and
needs no gate verdict. Feeding them to the entry gate would be a different
change requiring measurement first.

Note the system already has an INTRADAY level map with touch counts
(app/agents/levels.py), and a live tested-ceiling veto that consumes it
(sessions_service.py). This module is the prior-day/multi-day complement, and
the natural way to connect the two later would be to add these as extra entries
in that clustered map with a `source` tag — not to add a second veto.

The pivot/CPR formulas mirror app/research/level_features.py:126-134. They are
duplicated rather than imported because that module pulls pandas and sits under
a research-only no-lookahead assertion; these are stable textbook definitions.
Keep the two in step if either changes.
"""
from __future__ import annotations

from typing import Any, Sequence

# Nearest N levels either side of spot that get drawn. Twelve lines makes the
# chart pane unreadable; three a side is legible and still shows the box price
# is trading in.
MAX_PER_SIDE = 3

# Levels closer than this to spot are treated as "at price" and not drawn on
# either side.
_AT_SPOT_PCT = 0.01

# Two levels closer together than this are the same line to the eye, so only the
# higher-priority one is kept. Without this the pivot family swamps everything:
# RELIANCE on 2026-08-19 put CPR-B 1311.52, Pivot 1312.03 and CPR-T 1312.55
# within 0.08% of each other, and those three consumed every resistance slot.
_DEDUPE_PCT = 0.15

# Which levels earn a slot first. Prices the market actually traded (prior-day
# high/low, multi-day swings) beat prices derived by formula (pivots, CPR) —
# a level is only interesting because it was tested.
_PRIORITY = {"priorDay": 0, "swing": 1, "pivot": 2, "cpr": 3}


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def prior_day_levels(pdh: Any, pdl: Any, pdc: Any) -> dict[str, float]:
    """Classic floor pivots + Central Pivot Range from one prior day's OHLC."""
    h, l, c = _f(pdh), _f(pdl), _f(pdc)
    if h is None or l is None or c is None or h < l:
        return {}

    pivot = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * pivot - bc
    rng = h - l

    out = {
        "pdh": h, "pdl": l, "pdc": c,
        "pivot": pivot,
        "cprBot": min(bc, tc), "cprTop": max(bc, tc),
        "cprWidthPct": abs(tc - bc) / c * 100.0,
        "r1": 2 * pivot - l, "s1": 2 * pivot - h,
        "r2": pivot + rng,   "s2": pivot - rng,
        "r3": h + 2 * (pivot - l), "s3": l - 2 * (h - pivot),
    }
    return {k: round(v, 4) for k, v in out.items()}


def multi_day_levels(daily: Sequence[dict]) -> dict[str, float]:
    """Swing highs/lows over the last N sessions. `daily` is oldest-first."""
    out: dict[str, float] = {}
    for n in (5, 10, 15):
        window = [d for d in daily[-n:] if _f(d.get("high")) and _f(d.get("low"))]
        if len(window) < max(2, n // 2):     # too thin to call a swing level
            continue
        out[f"high{n}d"] = round(max(float(d["high"]) for d in window), 4)
        out[f"low{n}d"] = round(min(float(d["low"]) for d in window), 4)
    return out


_LABELS = {
    "pdh": ("PDH", "priorDay"), "pdl": ("PDL", "priorDay"), "pdc": ("PDC", "priorDay"),
    "pivot": ("Pivot", "pivot"),
    "cprTop": ("CPR-T", "cpr"), "cprBot": ("CPR-B", "cpr"),
    "r1": ("R1", "pivot"), "r2": ("R2", "pivot"), "r3": ("R3", "pivot"),
    "s1": ("S1", "pivot"), "s2": ("S2", "pivot"), "s3": ("S3", "pivot"),
    "high5d": ("5d High", "swing"), "low5d": ("5d Low", "swing"),
    "high10d": ("10d High", "swing"), "low10d": ("10d Low", "swing"),
    "high15d": ("15d High", "swing"), "low15d": ("15d Low", "swing"),
}


def build_levels(daily: Sequence[dict], spot: Any = None,
                 today: str | None = None) -> dict:
    """Full level set from daily bars, split into resistance and support.

    `daily` is oldest-first, as `fetch_daily` returns it, and must exclude the
    current session — the last row is the prior trading day.

    The split is done here rather than in the frontend so the chart does no
    financial reasoning: anything above `spot` is resistance, anything below is
    support. `spot` defaults to the prior close when not supplied.
    """
    rows = [d for d in (daily or []) if _f(d.get("high")) and _f(d.get("low"))]
    # Order by the data, never by a weekday walk-back: providers disagree on
    # date labelling (Groww stamps daily bars with a naive local timestamp and
    # the containers run UTC, so a label can shift a day). Sorting and taking
    # the last row makes "the prior session" a fact about the data.
    rows.sort(key=lambda d: str(d.get("date") or ""))
    if today is not None:
        # An in-progress session returns a PARTIAL bar whose high/low are only
        # today-so-far. Computing PDH off that is a lookahead bug that would be
        # silently wrong all day, so drop anything dated today or later.
        rows = [d for d in rows if str(d.get("date") or "") < str(today)]
    if not rows:
        return {}

    prev = rows[-1]
    values = prior_day_levels(prev.get("high"), prev.get("low"), prev.get("close"))
    if not values:
        return {}
    values.update(multi_day_levels(rows))

    ref = _f(spot) or values.get("pdc")
    if ref is None:
        return {}

    above, below = [], []
    for key, price in values.items():
        if key == "cprWidthPct" or key not in _LABELS:
            continue
        dist = (price - ref) / ref * 100.0
        # A level price is sitting exactly on is neither overhead nor underfoot,
        # and drawing it as either is misleading. Common on quiet days where the
        # pivot lands on the prior close.
        if abs(dist) < _AT_SPOT_PCT:
            continue
        label, kind = _LABELS[key]
        item = {"label": label, "price": price, "kind": kind,
                "distPct": round(dist, 3)}
        (above if dist > 0 else below).append(item)

    def _pick(items: list[dict]) -> list[dict]:
        """Nearest-first, but priority decides who gets a slot."""
        items.sort(key=lambda x: abs(x["distPct"]))       # nearest first
        ranked = sorted(items, key=lambda x: (_PRIORITY.get(x["kind"], 9),
                                              abs(x["distPct"])))
        kept: list[dict] = []
        for cand in ranked:
            if len(kept) >= MAX_PER_SIDE:
                break
            if any(abs(cand["price"] - k["price"]) / k["price"] * 100.0 < _DEDUPE_PCT
                   for k in kept):
                continue                                  # visually the same line
            kept.append(cand)
        kept.sort(key=lambda x: abs(x["distPct"]))        # present nearest-first
        return kept

    above, below = _pick(above), _pick(below)

    return {
        "asOf": str(prev.get("date") or ""),
        "days": len(rows),
        "ref": round(ref, 4),
        "resistance": above[:MAX_PER_SIDE],
        "support": below[:MAX_PER_SIDE],
        "pivot": values.get("pivot"),
        "cprTop": values.get("cprTop"),
        "cprBot": values.get("cprBot"),
        "cprWidthPct": values.get("cprWidthPct"),
    }
