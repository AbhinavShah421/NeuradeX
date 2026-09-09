"""Promotion grading — the statistics, not the plumbing.

The whole value of this module is refusing to overclaim, so the tests are mostly
about what it declines to say: pooling days, counting empty days, calling a
4-day result a finding.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workers.grading import (MIN_DAYS_FOR_A_CLAIM, T_ESTABLISHED, aggregate,
                                 cluster_t, day_lift, grade_one_day)


def syms(*names):
    return [{"symbol": n} for n in names]


# ── The lift, not the return ────────────────────────────────────────────────

def test_lift_is_measured_against_the_same_day_not_zero():
    """On a day everything rose 3%, a promoted set that rose 3% added nothing.
    Raw return would call that a win."""
    assert day_lift([3.0, 3.0], [3.0, 3.0]) == 0.0
    assert day_lift([4.0, 4.0], [3.0, 3.0]) == 1.0


def test_a_day_with_no_promotions_carries_no_information():
    """It must not count as a zero: that would drag the mean toward no-effect
    and inflate the day count at the same time."""
    assert day_lift([], [1.0, 2.0]) is None
    assert day_lift([1.0], []) is None


# ── Clustering by day ───────────────────────────────────────────────────────

def test_n_is_days_not_trades():
    """The 2026-08-11 audit's central finding: pooling observations inside a day
    treats one day's market as many independent samples."""
    out = cluster_t([0.5, 0.4, 0.6])
    assert out["n_days"] == 3          # three days, however many names each held


def test_empty_days_are_dropped_not_counted():
    out = cluster_t([0.5, None, 0.4, None])
    assert out["n_days"] == 2


def test_a_short_sample_is_never_called_a_finding():
    """A big t on four days is a number, not evidence — the audit needed ~75
    full-breadth days and had 4."""
    out = cluster_t([2.0, 2.1, 1.9, 2.0])       # huge, consistent, tiny sample
    assert out["t"] > T_ESTABLISHED
    assert "insufficient sample" in out["verdict"]


def test_a_long_consistent_positive_sample_is_established():
    lifts = [0.4, 0.5, 0.3, 0.45, 0.35] * 5      # 25 days, consistent
    out = cluster_t(lifts)
    assert out["n_days"] >= MIN_DAYS_FOR_A_CLAIM
    assert out["t"] > T_ESTABLISHED
    assert out["verdict"] == "established positive"


def test_a_long_negative_sample_is_established_negative():
    lifts = [-0.4, -0.5, -0.3, -0.45, -0.35] * 5
    assert cluster_t(lifts)["verdict"] == "established negative"


def test_a_noisy_long_sample_is_no_effect():
    """Mean near zero with real spread is the honest 'nothing here'."""
    lifts = [1.5, -1.4, 0.9, -1.1, 0.3] * 5
    out = cluster_t(lifts)
    assert out["n_days"] >= MIN_DAYS_FOR_A_CLAIM
    assert out["verdict"] == "no effect"


def test_the_bar_is_higher_than_the_usual_1_96():
    """Several promoter variants will be tried against one corpus; a 1.96 bar
    applied repeatedly finds an effect roughly every twentieth time."""
    assert T_ESTABLISHED > 1.96


# ── One day's grading ───────────────────────────────────────────────────────

def test_a_day_scores_both_controls_separately():
    """The promoter and the reviewer can each work or not, independently."""
    returns = {"A": 5.0, "B": 4.0, "R1": 1.0, "R2": 0.0, "F1": 2.0, "F2": 2.0}
    d = grade_one_day(syms("A", "B"), syms("R1", "R2"),
                      syms("F1", "F2", "A", "B", "R1", "R2"), returns)
    assert d["accepted"]["mean"] == 4.5
    assert d["rejected"]["mean"] == 0.5
    assert d["lift_vs_rejected"] == 4.0          # the reviewer separated them
    assert d["lift_vs_field"] == pytest.approx(4.5 - 2.3333, abs=0.01)


def test_an_unpriceable_symbol_is_dropped_not_zeroed():
    """A name that could not be priced is missing data. Calling it flat invents
    an outcome and drags every mean toward zero."""
    returns = {"A": 6.0}                          # B never priced
    d = grade_one_day(syms("A", "B"), syms(), syms("A", "B"), returns)
    assert d["accepted"]["n"] == 1
    assert d["accepted"]["mean"] == 6.0


def test_win_rate_is_reported_alongside_the_mean():
    """One 30% winner and four small losers is a very different set from five
    steady gainers, and the mean alone cannot tell them apart."""
    returns = {"A": 30.0, "B": -1.0, "C": -1.0, "D": -1.0, "E": -1.0}
    d = grade_one_day(syms("A", "B", "C", "D", "E"), syms(),
                      syms("A", "B", "C", "D", "E"), returns)
    assert d["accepted"]["mean"] > 0
    assert d["accepted"]["win_pct"] == 20.0


# ── The aggregate ───────────────────────────────────────────────────────────

def test_the_aggregate_reports_sample_size_before_any_claim():
    days = [{"lift_vs_field": 0.4, "lift_vs_rejected": 0.2,
             "accepted": {"n": 5}} for _ in range(3)]
    out = aggregate(days)
    assert out["days_graded"] == 3
    assert out["promotions_graded"] == 15
    assert "insufficient sample" in out["promoter"]["verdict"]


def test_promoter_and_reviewer_are_reported_separately():
    """A promoter can beat the field while the review contributes nothing —
    reporting one headline number would hide which half works.

    Lifts vary day to day, as real ones do: a constant series has zero variance
    and correctly reports 'no variance' rather than an infinite t, which is what
    the first draft of this test accidentally asserted against."""
    field_lifts = [0.4, 0.55, 0.35, 0.6, 0.45] * 5          # positive, with spread
    rejected_lifts = [0.1, -0.1, 0.05, -0.05, 0.0] * 5      # centred on nothing
    days = [{"lift_vs_field": f, "lift_vs_rejected": r, "accepted": {"n": 4}}
            for f, r in zip(field_lifts, rejected_lifts)]
    out = aggregate(days)
    assert out["promoter"]["verdict"] == "established positive"
    assert out["reviewer"]["verdict"] == "no effect"


def test_a_constant_series_reports_no_variance_rather_than_an_infinite_t():
    """Degenerate input must not produce a spectacular finding."""
    out = cluster_t([0.5] * 25)
    assert out["t"] is None
    assert out["verdict"] == "no variance"


def test_a_reviewer_that_adds_nothing_is_visible_as_such():
    """If accepted and rejected perform identically, the review is theatre and
    the grading has to say so rather than hiding behind the promoter's number."""
    days = [{"lift_vs_field": 0.6, "lift_vs_rejected": 0.0,
             "accepted": {"n": 3}} for _ in range(25)]
    assert aggregate(days)["reviewer"]["mean_lift"] == 0.0


def test_no_data_does_not_crash_or_claim():
    out = aggregate([])
    assert out["days_graded"] == 0
    assert out["promoter"]["verdict"] == "no data"
