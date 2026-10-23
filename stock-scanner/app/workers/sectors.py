"""Sector worker — which sectors are actually working today.

A sector's average return is the obvious metric and the misleading one. One
stock up 18% inside a nine-name sector produces the same average as all nine up
2%, and only the second is a sector move worth trading. So this reports BREADTH
alongside the move — what share of the sector's names are up — and ranks on a
figure that needs both.

The symbol→industry map comes from the backend's NSE-derived cache in Redis
(`ai_engine:sector_map:<date>`, ~750-2100 names). The scanner does not rebuild
it: one owner, one daily fetch. When the key is missing every symbol reads
"Other" and the worker degrades to a single bucket rather than failing — a
scanner that stops sweeping because a label lookup is cold would be a much worse
outcome than an unlabelled sweep.
"""
from __future__ import annotations

import statistics
from typing import Any, Iterable, Optional

# A sector needs at least this many analysed names before its numbers mean
# anything. Below it a single stock IS the sector, and its "sector move" is just
# that stock's move wearing a label.
_MIN_NAMES = 4

# Breadth this far from an even split is what separates a sector move from one
# name dragging an average. 0.65 = roughly two-thirds of the sector pointing the
# same way.
_BROAD = 0.65


def sector_breadth(rows: Iterable[dict], sector_of) -> dict[str, dict]:
    """Aggregate the sweep by sector.

    `sector_of` is injected rather than imported so the worker stays a pure
    function — tests pass a dict lookup, production passes the Redis-backed map.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        pct = r.get("change_pct")
        if pct is None:
            continue                     # unpriced name tells us nothing
        buckets.setdefault(sector_of(r.get("symbol", "")) or "Other", []).append(r)

    out: dict[str, dict] = {}
    for name, members in buckets.items():
        moves = [float(m["change_pct"]) for m in members if m.get("change_pct") is not None]
        if not moves:
            continue
        up = sum(1 for v in moves if v > 0)
        n = len(moves)
        # Median, not mean: one 18% name should not carry a sector, and the
        # median is exactly the statistic that refuses to let it.
        median = statistics.median(moves)
        vols = [float(m["rel_volume"]) for m in members
                if m.get("rel_volume") is not None]
        out[name] = {
            "sector": name,
            "names": n,
            "median_pct": round(median, 3),
            "mean_pct": round(sum(moves) / n, 3),
            "breadth": round(up / n, 3),           # share of the sector that is up
            "advancing": up,
            "declining": n - up,
            "rel_volume": round(statistics.median(vols), 2) if vols else None,
            "leaders": [m["symbol"] for m in
                        sorted(members, key=lambda x: -(x.get("change_pct") or 0))[:3]],
            "laggards": [m["symbol"] for m in
                         sorted(members, key=lambda x: (x.get("change_pct") or 0))[:3]],
            # Enough names to be a sector reading rather than a stock reading.
            "significant": n >= _MIN_NAMES,
        }
    return out


def _heat(s: dict) -> float:
    """Rank score: the move, weighted by how much of the sector agrees with it.

    Breadth enters as a signed multiplier centred on an even split, so a +1.5%
    median on 80% advancing outranks a +2.5% median on 45% — the second is one
    or two names and a lot of noise.
    """
    if not s.get("significant"):
        return float("-inf")             # never ranks; still reported
    conviction = (s["breadth"] - 0.5) * 2          # -1 .. +1
    return s["median_pct"] * max(0.0, conviction) if s["median_pct"] > 0 \
        else s["median_pct"] * max(0.0, -conviction)


def rank_sectors(rows: Iterable[dict], sector_of, top: int = 8) -> dict[str, Any]:
    """Hot and cold sectors for the day, plus the market-wide breadth reading.

    The `market` block is deliberately computed over EVERY analysed name rather
    than over the sectors, because sector aggregation drops anything the map
    could not label and the market reading should not inherit that gap.
    """
    rows = list(rows)
    sectors = sector_breadth(rows, sector_of)
    ranked = sorted(sectors.values(), key=_heat, reverse=True)
    significant = [s for s in ranked if s["significant"]]

    moves = [float(r["change_pct"]) for r in rows if r.get("change_pct") is not None]
    up = sum(1 for v in moves if v > 0)
    market = {
        "analysed": len(moves),
        "advancing": up,
        "declining": len(moves) - up,
        "breadth": round(up / len(moves), 3) if moves else None,
        "median_pct": round(statistics.median(moves), 3) if moves else None,
    }

    return {
        "market": market,
        "hot": [s for s in significant if s["median_pct"] > 0][:top],
        "cold": [s for s in reversed(significant) if s["median_pct"] < 0][:top],
        # Kept so a caller can ask about a specific sector without re-aggregating.
        "by_sector": sectors,
        "unlabelled": sectors.get("Other", {}).get("names", 0),
    }


def sector_tailwind(symbol: str, sector_of, ranked: dict) -> Optional[dict]:
    """Is this symbol's sector one of the ones working today?

    Used by the promotion reviewer: a name up 6% inside a sector up 3% on 80%
    breadth is a different proposition from the same name up 6% alone.
    """
    name = sector_of(symbol) or "Other"
    s = (ranked.get("by_sector") or {}).get(name)
    if not s or not s.get("significant"):
        return None
    return {
        "sector": name,
        "median_pct": s["median_pct"],
        "breadth": s["breadth"],
        "broad": s["breadth"] >= _BROAD,
        "supporting": s["median_pct"] > 0 and s["breadth"] >= _BROAD,
    }
