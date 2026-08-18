"""Step 3 of the edge-first build order: put every existing feature through the gate.

The system currently gives twelve agents and six indicators a say in live
decisions. None of them was individually shown to have edge before being given
that say. This module asks the question that was skipped, once per feature, with
the same verdict everywhere — `app.research.validation`.

Two families of candidate:

  agent votes     selected = the agent voted BUY (or SELL), baseline = it voted
                  HOLD on the same day. This isolates the agent's own opinion
                  from what the ensemble did with it, which is the whole point:
                  an agent can be right while being outvoted, and it can look
                  right purely because the gate only executed on days that
                  happened to go well.

  indicators      selected = the top quintile of the indicator that day,
                  baseline = the bottom quintile. Quintiles are cut *within* a
                  day, so a feature cannot score by drifting with the market.

Both are measured on `cf_pnl_pct`, the counterfactual label, which exists for
every decision rather than only for the ~0.7% the gate executed. Measuring on
executed trades instead would grade each feature on a sample the gate itself
selected — the bias that made the per-agent split on trade_records look striking
when it was nothing (see the co-signer investigation, 2026-08).

Run::

    python -m app.research.feature_survey            # since 2026-06-01
    python -m app.research.feature_survey 2026-08-10 # since broad capture began
"""
from __future__ import annotations

import asyncio
import json
import statistics
from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Sequence

from app.research.validation import Observation, Verdict, evaluate

# Indicators are levels, not signals: `sma5` at 416 says nothing on its own. Each
# is mapped to the scale-free form the agents actually reason about, so the
# survey tests the feature rather than the share price.
DERIVED_INDICATORS: dict[str, str] = {
    "rsi": "rsi",
    "atr": "atr",
    "momentum_pct": "momentum_pct",
    "price_vs_vwap_pct": "price relative to VWAP",
    "price_vs_sma20_pct": "price relative to SMA20",
    "sma5_vs_sma20_pct": "fast SMA relative to slow SMA",
}

_SQL = """
    SELECT created_at::date AS d, price, action, agents, indicators, cf_pnl_pct
    FROM session_decisions
    WHERE cf_pnl_pct IS NOT NULL
      AND created_at::date >= :since
"""


def _as_json(v: Any) -> Any:
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def derive_indicators(price: float | None, raw: dict[str, Any] | None) -> dict[str, float]:
    """Turn raw indicator levels into scale-free features."""
    if not raw or not price:
        return {}
    out: dict[str, float] = {}
    for k in ("rsi", "atr", "momentum_pct"):
        v = raw.get(k)
        if isinstance(v, (int, float)):
            out[k] = float(v)

    vwap, sma5, sma20 = raw.get("vwap"), raw.get("sma5"), raw.get("sma20")
    if isinstance(vwap, (int, float)) and vwap:
        out["price_vs_vwap_pct"] = 100.0 * (price - vwap) / vwap
    if isinstance(sma20, (int, float)) and sma20:
        out["price_vs_sma20_pct"] = 100.0 * (price - sma20) / sma20
        if isinstance(sma5, (int, float)):
            out["sma5_vs_sma20_pct"] = 100.0 * (sma5 - sma20) / sma20
    return out


# ── candidate construction ──────────────────────────────────────────────────

def agent_observations(rows: Sequence[dict], agent: str, side: str) -> list[Observation]:
    """`side` (BUY/SELL) votes from one agent, against that agent's own HOLDs."""
    obs: list[Observation] = []
    for r in rows:
        votes = r.get("_votes") or {}
        a = votes.get(agent)
        if a == side:
            obs.append(Observation(day=r["d"], pnl_pct=r["cf"], selected=True))
        elif a == "HOLD":
            obs.append(Observation(day=r["d"], pnl_pct=r["cf"], selected=False))
    return obs


def indicator_observations(rows: Sequence[dict], feature: str) -> list[Observation]:
    """Top quintile against bottom quintile, cut within each day.

    Cutting within the day is what stops the feature scoring on market drift: on
    a day everything rose, both quintiles rose, and the difference is still the
    feature's own contribution.
    """
    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        v = (r.get("_feat") or {}).get(feature)
        if v is not None:
            by_day[r["d"]].append((v, r["cf"]))

    obs: list[Observation] = []
    for day, pairs in by_day.items():
        if len(pairs) < 10:
            continue  # a quintile of fewer than two points is not a quintile
        pairs.sort(key=lambda p: p[0])
        cut = max(1, len(pairs) // 5)
        for _, pnl in pairs[-cut:]:
            obs.append(Observation(day=day, pnl_pct=pnl, selected=True))
        for _, pnl in pairs[:cut]:
            obs.append(Observation(day=day, pnl_pct=pnl, selected=False))
    return obs


# ── the survey ──────────────────────────────────────────────────────────────

async def load_rows(since: date) -> list[dict]:
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        raw = (await db.execute(text(_SQL), {"since": since})).fetchall()

    rows: list[dict] = []
    for d, price, _action, agents, indicators, cf in raw:
        votes = {
            a.get("agent"): (a.get("action") or "").upper()
            for a in (_as_json(agents) or [])
            if isinstance(a, dict) and a.get("agent")
        }
        rows.append({
            "d": d,
            "cf": float(cf),
            "_votes": votes,
            "_feat": derive_indicators(
                float(price) if price is not None else None, _as_json(indicators)
            ),
        })
    return rows


def survey(rows: Sequence[dict]) -> list[Verdict]:
    agents = sorted({a for r in rows for a in (r.get("_votes") or {})})
    verdicts: list[Verdict] = []

    for agent in agents:
        for side in ("BUY", "SELL"):
            obs = agent_observations(rows, agent, side)
            # An agent that never votes this side has baselines but no selections,
            # which is not a candidate at all — emitting it produces a row of nan
            # that reads like a measurement. `anomaly` never votes anything but
            # HOLD, so both its rows would otherwise appear in every report.
            if any(o.selected for o in obs):
                verdicts.append(evaluate(f"agent:{agent} {side}", obs))

    for feature in DERIVED_INDICATORS:
        obs = indicator_observations(rows, feature)
        if any(o.selected for o in obs):
            verdicts.append(evaluate(f"indicator:{feature} (top vs bottom quintile)", obs))

    return verdicts


def summarise(verdicts: Iterable[Verdict]) -> str:
    vs = list(verdicts)
    order = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}
    vs.sort(key=lambda v: (order[v.status], -abs(v.t_stat or 0)))

    width = max((len(v.name) for v in vs), default=20)
    lines = [
        f"{'candidate'.ljust(width)}  {'verdict':<13} {'days':>5} {'edge %/day':>11} "
        f"{'t':>6} {'xcost':>7}",
        "-" * (width + 48),
    ]
    for v in vs:
        lines.append(
            f"{v.name.ljust(width)}  {v.status:<13} {v.n_days:>5} "
            f"{v.mean_daily_edge_pct:>+11.4f} {v.t_stat:>+6.2f} {v.edge_over_cost:>+7.2f}"
        )

    counts = {s: sum(1 for v in vs if v.status == s) for s in ("PASS", "FAIL", "INCONCLUSIVE")}
    lines += [
        "",
        f"survivors: {counts['PASS']} PASS · {counts['FAIL']} FAIL · "
        f"{counts['INCONCLUSIVE']} INCONCLUSIVE  (of {len(vs)} candidates)",
    ]
    if vs:
        ts = [abs(v.t_stat) for v in vs if v.t_stat == v.t_stat]
        if ts:
            lines.append(
                f"median |t| = {statistics.median(ts):.2f}  "
                f"(pure noise would sit near 0.67)"
            )
    return "\n".join(lines)


async def main(since: date) -> None:
    from app.database.postgres import init_postgres
    await init_postgres()
    rows = await load_rows(since)
    print(f"loaded {len(rows):,} labelled decisions since {since}\n")
    print(summarise(survey(rows)))


if __name__ == "__main__":
    import sys
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 6, 1)
    asyncio.run(main(start))
