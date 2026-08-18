"""Tests for the feature survey.

The survey's job is to construct candidates honestly. Two constructions carry
the risk: an agent must be graded against its own HOLDs (not against everything),
and an indicator must be quintiled *within* a day (or it scores on market drift).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.research.feature_survey import (
    agent_observations,
    derive_indicators,
    indicator_observations,
    survey,
)


def _row(day: date, cf: float, votes: dict[str, str] | None = None,
         feat: dict[str, float] | None = None) -> dict:
    return {"d": day, "cf": cf, "_votes": votes or {}, "_feat": feat or {}}


D0 = date(2026, 1, 1)


# ── indicator derivation ────────────────────────────────────────────────────

def test_levels_become_scale_free_features():
    out = derive_indicators(105.0, {"vwap": 100.0, "sma20": 100.0, "sma5": 102.0, "rsi": 60.0})
    assert out["price_vs_vwap_pct"] == 5.0
    assert out["price_vs_sma20_pct"] == 5.0
    assert out["sma5_vs_sma20_pct"] == 2.0
    assert out["rsi"] == 60.0


def test_derivation_survives_missing_and_zero_inputs():
    assert derive_indicators(None, {"vwap": 100.0}) == {}
    assert derive_indicators(100.0, None) == {}
    assert "price_vs_vwap_pct" not in derive_indicators(100.0, {"vwap": 0})


# ── agent candidates ────────────────────────────────────────────────────────

def test_agent_is_graded_against_its_own_holds_not_everything():
    """A BUY must be compared with that agent saying HOLD — not with rows where
    it voted SELL, which would double-count the same opinion on both sides."""
    rows = [
        _row(D0, 1.0, {"technical": "BUY"}),
        _row(D0, 0.0, {"technical": "HOLD"}),
        _row(D0, 9.0, {"technical": "SELL"}),
    ]
    obs = agent_observations(rows, "technical", "BUY")
    assert [(o.pnl_pct, o.selected) for o in obs] == [(1.0, True), (0.0, False)]


def test_agent_that_never_votes_a_side_is_not_reported_as_a_candidate():
    """`anomaly` only ever votes HOLD. Its rows have baselines but no selections,
    which is not a measurement — the survey must omit it rather than print nan."""
    rows = [_row(D0, 1.0, {"anomaly": "HOLD"})]
    obs = agent_observations(rows, "anomaly", "BUY")
    assert obs and not any(o.selected for o in obs)
    assert [v for v in survey(rows) if v.name.startswith("agent:anomaly")] == []


# ── indicator candidates ────────────────────────────────────────────────────

def test_quintiles_are_cut_within_the_day_so_drift_cannot_score():
    """Two days with opposite drift but no within-day relationship must net out.

    If quintiles were cut across the pooled sample, the high-drift day would fill
    the top bucket and the feature would look predictive when it is not.
    """
    rows = []
    for i in range(10):                       # day 0: everything at +5, feature irrelevant
        rows.append(_row(D0, 5.0, feat={"f": float(i)}))
    for i in range(10):                       # day 1: everything at -5
        rows.append(_row(D0 + timedelta(days=1), -5.0, feat={"f": float(i)}))

    obs = indicator_observations(rows, "f")
    from app.research.validation import daily_edges
    assert set(daily_edges(obs).values()) == {0.0}


def test_within_day_relationship_is_detected():
    rows = [_row(D0, float(i), feat={"f": float(i)}) for i in range(10)]
    obs = indicator_observations(rows, "f")
    from app.research.validation import daily_edges
    assert daily_edges(obs)[D0] > 0            # top quintile out-returns bottom


def test_days_too_thin_to_quintile_are_skipped():
    rows = [_row(D0, 1.0, feat={"f": float(i)}) for i in range(4)]
    assert indicator_observations(rows, "f") == []


# ── end to end ──────────────────────────────────────────────────────────────

def test_survey_covers_every_agent_and_side_it_sees():
    rows = [
        _row(D0, 1.0, {"technical": "BUY", "pattern": "HOLD"}, {"rsi": 50.0}),
        _row(D0, 0.0, {"technical": "HOLD", "pattern": "SELL"}, {"rsi": 40.0}),
    ]
    names = {v.name for v in survey(rows)}
    assert "agent:technical BUY" in names
    assert "agent:pattern SELL" in names


def test_thin_sample_is_inconclusive_never_pass():
    """The survey must not hand back a PASS on a handful of days."""
    rows = [
        _row(D0 + timedelta(days=i), 5.0, {"technical": "BUY"})
        for i in range(5)
    ] + [
        _row(D0 + timedelta(days=i), 0.0, {"technical": "HOLD"})
        for i in range(5)
    ]
    verdicts = survey(rows)
    assert verdicts
    assert all(v.status == "INCONCLUSIVE" for v in verdicts)
