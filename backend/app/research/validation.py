"""The validation gate. One harness, one verdict, no exceptions.

Nothing may change live trading behaviour without passing through here first.

Why this exists
---------------
Not to fix a clustering bug in the existing studies. half_life, level_report,
mean_reversion_entry and short_side all already aggregate to per-day means
before taking a t-statistic, which is correct — intraday decisions are not
independent draws, since thousands of them share a single day's market move, so
the effective sample size is the number of *days*, not the number of rows.

It exists because that verdict is currently expressed four ad-hoc ways, and
because none of those four applies the three checks that decide whether a
finding is tradeable rather than merely real:

  * a cost hurdle — edge must beat 3x round-trip cost, not merely zero
  * an out-of-sample sign check on a chronological holdout
  * a MIN_DAYS floor, so "not enough data" is never reported as "no effect"

One harness, one verdict, one place to change the rules.

Three outcomes, and INCONCLUSIVE is a real one
----------------------------------------------
    PASS          edge clears the cost hurdle with day-clustered significance
                  and the out-of-sample half agrees on sign
    FAIL          measured, and it does not clear
    INCONCLUSIVE  not enough days to distinguish either way — do not ship,
                  and do not treat as refuted

Usage::

    from app.research.validation import Observation, evaluate

    obs = [Observation(day=d, pnl_pct=p, selected=is_buy) for ...]
    verdict = evaluate("buy_vs_hold", obs)
    print(verdict.report())
    if verdict.status == "PASS":
        ...
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Sequence

# ── Gate parameters ─────────────────────────────────────────────────────────
# Minimum distinct trading days before a verdict is anything but INCONCLUSIVE.
# 60 is the floor at which a modest edge becomes detectable against the daily
# variance measured on this universe; broad-breadth capture began 2026-08-10, so
# expect INCONCLUSIVE on most things until roughly November 2026.
MIN_DAYS = 60

# Edge must beat round-trip cost by this multiple. 1.0x is break-even on a cost
# estimate that is itself uncertain; 3x leaves room to be wrong about both the
# edge and the slippage.
HURDLE_MULT = 3.0

# Fraction of days (chronologically first) used as in-sample. The remainder is
# held out and must agree on the sign of the edge.
OOS_SPLIT = 0.6

Status = Literal["PASS", "FAIL", "INCONCLUSIVE"]


@dataclass(frozen=True)
class Observation:
    """One decision and what it was worth.

    `pnl_pct` is the realised or counterfactual return in percent, already net of
    slippage and charges if it came from `cf_pnl_pct` (it is — see
    counterfactual._simulate_policy). `selected` marks whether the candidate
    signal fired; unselected rows form the baseline it must beat.
    """

    day: date
    pnl_pct: float
    selected: bool


@dataclass(frozen=True)
class Verdict:
    name: str
    status: Status
    n_days: int
    n_selected: int
    n_baseline: int
    mean_daily_edge_pct: float
    sd_daily_edge_pct: float
    t_stat: float
    p_value: float
    ci95_pct: tuple[float, float]
    cost_pct: float
    hurdle_pct: float
    edge_over_cost: float
    oos_days: int
    oos_mean_edge_pct: float | None
    oos_sign_agrees: bool | None
    reasons: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"{self.name}: {self.status}",
            f"  days            {self.n_days}   (selected {self.n_selected:,} / baseline {self.n_baseline:,})",
            f"  daily edge      {self.mean_daily_edge_pct:+.4f}%  sd {self.sd_daily_edge_pct:.4f}",
            f"  95% CI          [{self.ci95_pct[0]:+.4f}%, {self.ci95_pct[1]:+.4f}%]",
            f"  t / p           {self.t_stat:+.2f}  /  {self.p_value:.3f}",
            f"  cost / hurdle   {self.cost_pct:.4f}% / {self.hurdle_pct:.4f}%   edge is {self.edge_over_cost:.2f}x cost",
        ]
        if self.oos_mean_edge_pct is not None:
            agree = "agrees" if self.oos_sign_agrees else "DISAGREES"
            lines.append(
                f"  out-of-sample   {self.oos_mean_edge_pct:+.4f}% over {self.oos_days} days — sign {agree}"
            )
        for r in self.reasons:
            lines.append(f"  · {r}")
        return "\n".join(lines)


# ── statistics ──────────────────────────────────────────────────────────────

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _two_sided_p(t: float, df: int) -> float:
    """Normal approximation to the two-sided p-value.

    Deliberately approximate: at the day counts this gate demands (>= 60) the
    normal and t distributions differ in the third decimal, and no decision here
    turns on that. Kept dependency-free so the gate has no import cost.
    """
    if not math.isfinite(t):
        return float("nan")
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


def daily_edges(observations: Iterable[Observation]) -> dict[date, float]:
    """Collapse observations to one edge per day.

    A day contributes only if it has both a selected and a baseline observation —
    otherwise "edge over baseline" is undefined for that day and including it
    would smuggle in the day's raw drift as if it were signal.
    """
    sel: dict[date, list[float]] = {}
    base: dict[date, list[float]] = {}
    for o in observations:
        (sel if o.selected else base).setdefault(o.day, []).append(o.pnl_pct)

    return {
        d: _mean(sel[d]) - _mean(base[d])
        for d in sorted(sel.keys() & base.keys())
        if sel[d] and base[d]
    }


# ── the gate ────────────────────────────────────────────────────────────────

def evaluate(
    name: str,
    observations: Sequence[Observation],
    *,
    min_days: int = MIN_DAYS,
    hurdle_mult: float = HURDLE_MULT,
    oos_split: float = OOS_SPLIT,
    cost_pct: float | None = None,
) -> Verdict:
    """Day-clustered, cost-netted, out-of-sample-checked verdict on one candidate."""
    if cost_pct is None:
        from app.utils.trade_costs import round_trip_cost_pct
        cost_pct = round_trip_cost_pct()
    hurdle = hurdle_mult * cost_pct

    edges_by_day = daily_edges(observations)
    days = sorted(edges_by_day)
    edges = [edges_by_day[d] for d in days]
    n_days = len(days)
    n_sel = sum(1 for o in observations if o.selected)
    n_base = len(observations) - n_sel

    reasons: list[str] = []

    if n_days < 3:
        return Verdict(
            name=name, status="INCONCLUSIVE", n_days=n_days,
            n_selected=n_sel, n_baseline=n_base,
            mean_daily_edge_pct=float("nan"), sd_daily_edge_pct=float("nan"),
            t_stat=float("nan"), p_value=float("nan"),
            ci95_pct=(float("nan"), float("nan")),
            cost_pct=cost_pct, hurdle_pct=hurdle, edge_over_cost=float("nan"),
            oos_days=0, oos_mean_edge_pct=None, oos_sign_agrees=None,
            reasons=[f"only {n_days} usable day(s) — a day needs both a selected "
                     f"and a baseline observation to yield an edge"],
        )

    mean_edge = _mean(edges)
    sd = _stdev(edges)
    se = sd / math.sqrt(n_days) if sd > 0 else float("nan")
    t = mean_edge / se if se and math.isfinite(se) and se > 0 else float("nan")
    p = _two_sided_p(t, n_days - 1)
    half = 1.96 * se if math.isfinite(se) else float("nan")
    ci = (mean_edge - half, mean_edge + half)
    edge_over_cost = mean_edge / cost_pct if cost_pct > 0 else float("nan")

    # Out-of-sample: chronological split, held-out half must agree on sign.
    cut = int(n_days * oos_split)
    oos_days_list = days[cut:]
    oos_mean: float | None = None
    oos_agrees: bool | None = None
    if len(oos_days_list) >= 3:
        oos_mean = _mean([edges_by_day[d] for d in oos_days_list])
        oos_agrees = (oos_mean > 0) == (mean_edge > 0)

    # ── verdict ─────────────────────────────────────────────────────────────
    if n_days < min_days:
        reasons.append(
            f"only {n_days} days — the gate needs {min_days} before it will call "
            f"anything either way. Not a refutation; keep collecting."
        )
        status: Status = "INCONCLUSIVE"
    else:
        failures = []
        if not math.isfinite(t) or abs(t) < 2.0:
            failures.append(f"day-clustered t={t:+.2f} does not clear |t| >= 2.0")
        if mean_edge <= 0:
            failures.append(f"edge is {mean_edge:+.4f}% — not positive")
        if mean_edge < hurdle:
            failures.append(
                f"edge {mean_edge:+.4f}% is below the {hurdle_mult:g}x cost hurdle of {hurdle:.4f}%"
            )
        if oos_agrees is False:
            failures.append(
                f"out-of-sample edge {oos_mean:+.4f}% flips sign against in-sample"
            )
        if failures:
            status = "FAIL"
            reasons.extend(failures)
        else:
            status = "PASS"
            reasons.append(
                f"clears {hurdle_mult:g}x cost with t={t:+.2f} over {n_days} days, "
                f"out-of-sample sign holds"
            )

    return Verdict(
        name=name, status=status, n_days=n_days,
        n_selected=n_sel, n_baseline=n_base,
        mean_daily_edge_pct=mean_edge, sd_daily_edge_pct=sd,
        t_stat=t, p_value=p, ci95_pct=ci,
        cost_pct=cost_pct, hurdle_pct=hurdle, edge_over_cost=edge_over_cost,
        oos_days=len(oos_days_list), oos_mean_edge_pct=oos_mean,
        oos_sign_agrees=oos_agrees, reasons=reasons,
    )


# ── loading real observations ───────────────────────────────────────────────

_DECISIONS_SQL = """
    SELECT created_at::date AS d, action, cf_pnl_pct
    FROM session_decisions
    WHERE cf_pnl_pct IS NOT NULL
      AND created_at::date >= :since
"""


async def observations_from_decisions(
    since: date,
    *,
    selected_actions: Sequence[str] = ("BUY",),
    baseline_actions: Sequence[str] = ("HOLD",),
) -> list[Observation]:
    """Pull counterfactually-labelled decisions as Observations.

    `cf_pnl_pct` is already net of slippage and charges, so a verdict built on it
    is directly comparable to the cost hurdle.
    """
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal

    sel = {a.upper() for a in selected_actions}
    base = {a.upper() for a in baseline_actions}

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(_DECISIONS_SQL), {"since": since})).fetchall()

    out: list[Observation] = []
    for day, action, pnl in rows:
        a = (action or "").upper()
        if a in sel:
            out.append(Observation(day=day, pnl_pct=float(pnl), selected=True))
        elif a in base:
            out.append(Observation(day=day, pnl_pct=float(pnl), selected=False))
    return out
