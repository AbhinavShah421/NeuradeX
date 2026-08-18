"""Tests for net expectancy.

The property that matters most: expectancy must NOT improve when the exit
geometry is tightened to manufacture a higher win rate. That is the whole reason
it replaces win rate as the objective.
"""
from __future__ import annotations

import math

from app.utils.expectancy import compute


def test_tightening_the_target_lifts_win_rate_without_lifting_expectancy():
    """The anti-gaming property.

    Same underlying market, two exit geometries. The tight-target book wins 90%
    of the time and the wide one 33%, yet the tight book is the worse strategy.
    A win-rate objective prefers it; expectancy does not.
    """
    tight = [0.10] * 90 + [-1.00] * 10      # 90% win rate
    wide = [3.00] * 33 + [-1.00] * 67       # 33% win rate

    t = compute(tight, cost_pct=0.0)
    w = compute(wide, cost_pct=0.0)

    assert t.win_rate > w.win_rate                      # tight looks far better
    assert t.net_expectancy_pct < w.net_expectancy_pct  # and is far worse


def test_costs_can_flip_a_profitable_gross_strategy():
    r = [0.10] * 60 + [-0.10] * 40           # gross +0.02%/trade
    assert compute(r, cost_pct=0.0).profitable
    assert not compute(r, cost_pct=0.125).profitable


def test_breakeven_win_rate_is_the_bar_the_payoff_sets():
    """A 1:1 payoff with zero costs needs better than half the trades to win."""
    r = [1.0] * 50 + [-1.0] * 50
    e = compute(r, cost_pct=0.0)
    assert math.isclose(e.payoff_ratio, 1.0)
    assert math.isclose(e.breakeven_win_rate, 0.5)
    assert math.isclose(e.net_expectancy_pct, 0.0, abs_tol=1e-9)


def test_breakeven_rises_with_costs():
    r = [1.0] * 50 + [-1.0] * 50
    assert compute(r, cost_pct=0.20).breakeven_win_rate > 0.5


def test_measured_live_geometry_is_below_cost():
    """The system's own numbers: 33% win, +0.0435 / -0.0125, against 0.125% cost."""
    r = [0.0435] * 331 + [-0.0125] * 669
    e = compute(r, cost_pct=0.125)
    assert 3.4 < e.payoff_ratio < 3.6
    assert e.gross_expectancy_pct > 0          # gross edge is positive
    assert not e.profitable                    # and nowhere near covering costs
    assert e.edge_over_cost < 0.1


def test_daily_sharpe_uses_days_not_trades():
    """Two days of identical trades must not look like many independent samples."""
    returns = [1.0, 1.0, 1.0, -1.0, -1.0, -1.0, 0.5, 0.5, 0.5]
    days = ["d1"] * 3 + ["d2"] * 3 + ["d3"] * 3
    e = compute(returns, cost_pct=0.0, day_of=days)
    assert e.daily_sharpe is not None
    # Nine trades collapse to three daily totals: +3, -3, +1.5. Sample sd
    # (ddof=1) is the right denominator for a Sharpe estimated from a sample.
    import statistics as _st
    dailies = [3.0, -3.0, 1.5]
    assert math.isclose(e.daily_sharpe, _st.mean(dailies) / _st.stdev(dailies), rel_tol=1e-9)


def test_sharpe_is_none_without_days_or_with_too_few():
    assert compute([1.0, -1.0, 0.5], cost_pct=0.0).daily_sharpe is None
    assert compute([1.0, -1.0], cost_pct=0.0, day_of=["a", "b"]).daily_sharpe is None


def test_empty_and_non_finite_input_is_safe():
    assert compute([]).n == 0
    assert compute([float("nan"), None, float("inf")]).n == 0  # type: ignore[list-item]


def test_all_winners_has_no_loss_side():
    e = compute([1.0, 2.0], cost_pct=0.0)
    assert e.win_rate == 1.0
    assert e.avg_loss_pct == 0.0
    assert e.payoff_ratio == float("inf")
    assert e.profitable


def test_as_dict_is_json_safe_and_rounded():
    d = compute([1.0, -0.5, 0.25], cost_pct=0.125).as_dict()
    assert d["n"] == 3
    assert isinstance(d["profitable"], bool)
    assert d["daily_sharpe"] is None
