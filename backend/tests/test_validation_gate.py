"""Tests for the validation gate.

The gate exists to refuse things, so most of these tests are about refusing.
Two properties carry the weight: a verdict must not strengthen just because a
day contributed more rows, and statistical significance alone must never be
enough to pass.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from app.research.validation import (
    Observation,
    daily_edges,
    evaluate,
)


def _days(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _obs(day: date, selected: list[float], baseline: list[float]) -> list[Observation]:
    return (
        [Observation(day=day, pnl_pct=v, selected=True) for v in selected]
        + [Observation(day=day, pnl_pct=v, selected=False) for v in baseline]
    )


# ── day clustering ──────────────────────────────────────────────────────────

def test_daily_edges_collapses_to_one_value_per_day():
    d = date(2026, 1, 1)
    obs = _obs(d, selected=[1.0, 3.0], baseline=[0.0, 2.0])
    assert daily_edges(obs) == {d: 1.0}


def test_day_without_both_sides_is_dropped():
    """A day with no baseline has no defined edge — including it would smuggle in
    that day's raw market drift as though it were signal."""
    d1, d2 = date(2026, 1, 1), date(2026, 1, 2)
    obs = _obs(d1, selected=[1.0], baseline=[0.0])
    obs += [Observation(day=d2, pnl_pct=99.0, selected=True)]  # no baseline
    edges = daily_edges(obs)
    assert set(edges) == {d1}


def test_clustering_does_not_inflate_t_with_more_rows_per_day():
    """Same daily edges, 100x the rows — t must not move.

    A naive `t = mean / (sd / sqrt(len(obs)))` over raw rows would multiply t by
    10 here. Sampling a day more densely is not new evidence about that day.
    """
    days = _days(80)
    rng = random.Random(7)
    daily = [rng.gauss(0.02, 0.30) for _ in days]

    def build(rows_per_day: int):
        obs: list[Observation] = []
        for d, edge in zip(days, daily):
            obs += _obs(d, selected=[edge] * rows_per_day, baseline=[0.0] * rows_per_day)
        return evaluate("x", obs, cost_pct=0.001)

    thin, fat = build(1), build(100)
    assert thin.n_days == fat.n_days == 80
    assert fat.n_selected == 100 * thin.n_selected
    assert math.isclose(thin.t_stat, fat.t_stat, rel_tol=1e-9)


# ── the three verdicts ──────────────────────────────────────────────────────

def test_too_few_days_is_inconclusive_not_fail():
    """Absence of evidence must not be recorded as evidence of absence."""
    days = _days(10)
    obs = [o for d in days for o in _obs(d, selected=[5.0], baseline=[0.0])]
    v = evaluate("strong_but_short", obs, cost_pct=0.06)
    assert v.status == "INCONCLUSIVE"
    assert "60" in " ".join(v.reasons)


def test_real_edge_over_enough_days_passes():
    days = _days(90)
    rng = random.Random(3)
    obs: list[Observation] = []
    for d in days:
        obs += _obs(d, selected=[0.40 + rng.gauss(0, 0.05)], baseline=[0.0])
    v = evaluate("real_edge", obs, cost_pct=0.06)
    assert v.status == "PASS", v.report()
    assert v.edge_over_cost > 3.0


def _noise_verdict(seed: int, cost_pct: float = 0.06):
    """90 days of pure noise — the measured state of the live ensemble."""
    days = _days(90)
    rng = random.Random(seed)
    obs: list[Observation] = []
    for d in days:
        e = rng.gauss(0.0, 0.28)
        obs += _obs(d, selected=[e] * 50, baseline=[0.0] * 50)
    return evaluate(f"noise_{seed}", obs, cost_pct=cost_pct)


def test_pure_noise_never_passes_across_many_seeds():
    """No amount of data turns noise into an edge. 4,500 selected rows per run."""
    verdicts = [_noise_verdict(s) for s in range(40)]
    assert all(v.status == "FAIL" for v in verdicts)
    assert all(v.n_selected == 4500 for v in verdicts)


def test_cost_hurdle_catches_the_noise_runs_that_clear_significance():
    """Significance alone is not enough, and this is why the hurdle is a second gate.

    Seed 11 draws a 1-in-20 fluke: t = +2.36 on data with no edge whatsoever.
    A gate that stopped at |t| >= 2 would have shipped it. The hurdle does not,
    because a 0.068% daily edge cannot pay a 0.18% cost hurdle.
    """
    v = _noise_verdict(11)
    assert abs(v.t_stat) > 2.0          # noise cleared significance
    assert v.status == "FAIL"           # and was still refused
    assert any("hurdle" in r for r in v.reasons)


def test_edge_that_is_significant_but_under_the_cost_hurdle_fails():
    """A tiny, perfectly consistent edge is still not tradeable."""
    days = _days(90)
    obs = [o for d in days for o in _obs(d, selected=[0.05], baseline=[0.0])]
    v = evaluate("under_hurdle", obs, cost_pct=0.06)
    assert v.status == "FAIL"
    assert any("hurdle" in r for r in v.reasons)
    assert v.edge_over_cost < 3.0


def test_negative_edge_fails_even_with_a_large_t():
    days = _days(90)
    obs = [o for d in days for o in _obs(d, selected=[-0.40], baseline=[0.0])]
    v = evaluate("inverted", obs, cost_pct=0.06)
    assert v.status == "FAIL"
    assert any("not positive" in r for r in v.reasons)


def test_out_of_sample_sign_flip_fails_an_otherwise_passing_candidate():
    """Strong in-sample, reversed out-of-sample — the classic overfit signature."""
    days = _days(100)
    obs: list[Observation] = []
    for i, d in enumerate(days):
        edge = 0.60 if i < 60 else -0.60
        obs += _obs(d, selected=[edge], baseline=[0.0])
    v = evaluate("overfit", obs, cost_pct=0.06)
    assert v.status == "FAIL"
    assert v.oos_sign_agrees is False
    assert any("out-of-sample" in r for r in v.reasons)


# ── reporting ───────────────────────────────────────────────────────────────

def test_report_is_readable_and_states_the_status():
    days = _days(90)
    obs = [o for d in days for o in _obs(d, selected=[0.40], baseline=[0.0])]
    text = evaluate("readable", obs, cost_pct=0.06).report()
    assert "readable: PASS" in text
    assert "cost hurdle" not in text  # no failure reasons on a pass
    assert "x cost" in text


@pytest.mark.parametrize("cost,expected", [(0.001, "PASS"), (0.20, "FAIL")])
def test_verdict_moves_with_the_cost_assumption(cost, expected):
    """Cost is a gate input, not a footnote — the same signal flips verdict."""
    days = _days(90)
    obs = [o for d in days for o in _obs(d, selected=[0.30], baseline=[0.0])]
    assert evaluate("cost_sensitive", obs, cost_pct=cost).status == expected


# ── memory-bounded entry point ──────────────────────────────────────────────

def test_evaluate_from_daily_edges_matches_evaluate():
    """The streaming path must give the identical verdict to the buffered one.

    `evaluate` holds every Observation; `evaluate_from_daily_edges` takes only
    the collapsed per-day values, which is what makes a full sweep fit in memory.
    They must not drift apart.
    """
    from app.research.validation import daily_edges, evaluate_from_daily_edges

    days = _days(90)
    rng = random.Random(5)
    obs: list[Observation] = []
    for d in days:
        obs += _obs(d, selected=[0.40 + rng.gauss(0, 0.1)] * 7, baseline=[0.0] * 7)

    buffered = evaluate("x", obs, cost_pct=0.06)
    streamed = evaluate_from_daily_edges("x", daily_edges(obs), cost_pct=0.06)

    assert streamed.status == buffered.status
    assert math.isclose(streamed.mean_daily_edge_pct, buffered.mean_daily_edge_pct)
    assert math.isclose(streamed.t_stat, buffered.t_stat)
    assert streamed.oos_mean_edge_pct == buffered.oos_mean_edge_pct
    assert streamed.n_days == buffered.n_days
