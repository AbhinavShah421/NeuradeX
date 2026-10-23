"""Net expectancy — what a trade is worth on average, after costs.

Why this replaces win rate as the headline
------------------------------------------
Win rate is a dial on the exit geometry, not a measure of skill. Take profit at
+0.05% and stop at -1.00% and you will win nine trades in ten while losing money
steadily. Optimising it directly pushes toward cutting winners and holding
losers, which is the failure mode already visible in this system's exit history.

Expectancy cannot be gamed that way, because tightening the target to lift the
win rate shrinks `avg_win` by exactly enough to cancel it:

    gross = win_rate * avg_win - (1 - win_rate) * avg_loss
    net   = gross - round_trip_cost

Win rate is still reported — it is a useful description of a strategy's shape.
It is simply no longer the thing anything is tuned against.

`breakeven_win_rate` is the companion number: given the payoff ratio a strategy
actually achieves, the win rate it must clear to pay for itself. Comparing it to
the realised win rate says whether the geometry or the accuracy is the problem.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Expectancy:
    n: int
    win_rate: float                 # reported, never targeted
    avg_win_pct: float
    avg_loss_pct: float             # positive magnitude
    payoff_ratio: float             # avg_win / avg_loss
    gross_expectancy_pct: float
    cost_pct: float
    net_expectancy_pct: float
    edge_over_cost: float           # gross / cost — how much room there is to be wrong
    breakeven_win_rate: float       # win rate this payoff needs to cover costs
    daily_sharpe: float | None
    profitable: bool

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "win_rate": round(self.win_rate, 4),
            "avg_win_pct": round(self.avg_win_pct, 4),
            "avg_loss_pct": round(self.avg_loss_pct, 4),
            "payoff_ratio": round(self.payoff_ratio, 3),
            "gross_expectancy_pct": round(self.gross_expectancy_pct, 5),
            "cost_pct": round(self.cost_pct, 4),
            "net_expectancy_pct": round(self.net_expectancy_pct, 5),
            "edge_over_cost": round(self.edge_over_cost, 3),
            "breakeven_win_rate": round(self.breakeven_win_rate, 4),
            "daily_sharpe": (round(self.daily_sharpe, 3)
                             if self.daily_sharpe is not None else None),
            "profitable": self.profitable,
        }


_EMPTY = Expectancy(
    n=0, win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0, payoff_ratio=0.0,
    gross_expectancy_pct=0.0, cost_pct=0.0, net_expectancy_pct=0.0,
    edge_over_cost=0.0, breakeven_win_rate=0.0, daily_sharpe=None,
    profitable=False,
)


def compute(
    returns_pct: Sequence[float],
    *,
    cost_pct: float | None = None,
    day_of: Mapping[int, object] | Sequence[object] | None = None,
) -> Expectancy:
    """Expectancy over a sequence of per-trade returns, expressed in percent.

    `returns_pct` must already be percent (2.45 for 2.45%), not fractions —
    trade_records stores fractions, so callers convert.

    `day_of` optionally supplies the trading day of each return, in the same
    order, which enables a daily Sharpe. Per-trade Sharpe is deliberately not
    offered: intraday trades within a day are correlated, so it overstates the
    ratio for the same reason a per-observation t-statistic overstates
    significance.
    """
    vals = [float(r) for r in returns_pct if r is not None and math.isfinite(float(r))]
    if not vals:
        return _EMPTY

    if cost_pct is None:
        from app.utils.trade_costs import round_trip_cost_pct
        cost_pct = round_trip_cost_pct()

    wins = [v for v in vals if v > 0]
    losses = [-v for v in vals if v <= 0]      # positive magnitudes

    n = len(vals)
    win_rate = len(wins) / n
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else float("inf") if avg_win > 0 else 0.0

    gross = win_rate * avg_win - (1.0 - win_rate) * avg_loss
    net = gross - cost_pct

    # The win rate this payoff ratio needs in order to cover costs. Expressed
    # against avg_win/avg_loss so it is comparable with the realised win rate.
    denom = avg_win + avg_loss
    breakeven = ((avg_loss + cost_pct) / denom) if denom > 0 else 1.0

    daily_sharpe = None
    if day_of is not None:
        by_day: dict[object, list[float]] = {}
        for v, d in zip(vals, list(day_of)):
            by_day.setdefault(d, []).append(v)
        dailies = [sum(x) for x in by_day.values()]
        if len(dailies) >= 3:
            sd = statistics.stdev(dailies)
            if sd > 0:
                daily_sharpe = statistics.mean(dailies) / sd

    return Expectancy(
        n=n,
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        payoff_ratio=payoff,
        gross_expectancy_pct=gross,
        cost_pct=cost_pct,
        net_expectancy_pct=net,
        edge_over_cost=(gross / cost_pct) if cost_pct > 0 else float("inf"),
        breakeven_win_rate=min(breakeven, 1.0),
        daily_sharpe=daily_sharpe,
        profitable=net > 0,
    )
