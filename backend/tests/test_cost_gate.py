"""Tests for the cost viability gate.

The gate answers one arithmetic question: does this instrument's own range move
enough to pay for getting in and out? It says nothing about direction.

It is deliberately NOT wired into the live entry path. Measured over 295,489
decisions it would block 98.9% of them, and the 1.1% it admits have a WORSE mean
counterfactual outcome (-0.340%) than the ones it blocks (-0.126%). Shipping it
as a filter would shut the system down and select the wrong subset. The
measurement is the finding, not the filter — see the commit and the register.
"""
from __future__ import annotations

from app.utils.trade_costs import (
    ATR_CAPTURE,
    COST_HURDLE_MULT,
    covers_cost,
    expected_move_pct,
    round_trip_cost_pct,
)


def test_expected_move_scales_with_atr_and_capture():
    assert expected_move_pct(100.0, 1.0, capture=1.0) == 1.0
    assert expected_move_pct(100.0, 1.0, capture=0.5) == 0.5
    assert expected_move_pct(200.0, 1.0, capture=1.0) == 0.5


def test_missing_or_absurd_inputs_fail_closed():
    """An unknown range is not evidence of a tradeable one."""
    for price, atr in ((0.0, 1.0), (100.0, 0.0), (-5.0, 1.0), (100.0, -1.0)):
        assert expected_move_pct(price, atr) == 0.0
        assert covers_cost(price, atr)[0] is False


def test_a_wide_range_instrument_clears_the_hurdle():
    # 1% ATR on a 100 rupee stock -> 0.7% expected capture vs a 0.375% hurdle
    passes, move, hurdle = covers_cost(100.0, 1.0)
    assert passes
    assert move > hurdle


def test_the_median_instrument_in_this_universe_does_not_clear_it():
    """The finding, pinned as a test.

    The median decision in this system sees an ATR of ~0.096% of price. At the
    measured 0.7 capture that is ~0.067% against a 0.375% hurdle — the trade
    cannot pay for itself however good the signal is.
    """
    price = 1000.0
    median_atr = price * 0.00096
    passes, move, hurdle = covers_cost(price, median_atr)
    assert not passes
    assert move < hurdle / 4          # not marginal — off by a factor of four


def test_even_a_breakeven_hurdle_excludes_the_median_instrument():
    """Not an artifact of demanding 3x. At 1x cost the median still fails."""
    price = 1000.0
    median_atr = price * 0.00096
    assert not covers_cost(price, median_atr, hurdle_mult=1.0)[0]


def test_hurdle_tracks_the_configured_cost_model():
    _, _, hurdle = covers_cost(100.0, 1.0)
    assert abs(hurdle - COST_HURDLE_MULT * round_trip_cost_pct()) < 1e-9


def test_capture_default_is_below_one_atr():
    """A full ATR is optimistic; the default is the realised win/ATR ratio."""
    assert 0.0 < ATR_CAPTURE < 1.0
