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


# ── Brokerage is not a basis-point constant ──────────────────────────────────
# Added 2026-09-10. The model charged a flat 3 bps/side and reported a 0.125%
# round trip. Groww charges the LOWER of Rs20 or 0.05% per order, so the rate
# depends on order value — and at the ~Rs5,000 positions this system actually
# trades it is 5 bps/side, not 3. Stamp duty was missing entirely. Every net-of-
# cost figure in the project was flattered by ~1.5x, against a measured gross
# intraday edge of +0.030%/trade.

from app.utils.trade_costs import brokerage, charges, cost_floor_pct


def test_brokerage_takes_the_lower_of_cap_and_percentage():
    assert brokerage(5_000) == 2.50        # 0.05% binds
    assert brokerage(40_000) == 20.0       # the crossover — both give Rs20
    assert brokerage(200_000) == 20.0      # cap binds
    assert brokerage(0) == 0.0
    assert brokerage(-1) == 0.0            # never a negative charge


def test_cost_rate_falls_only_above_the_cap_crossover():
    """Sizing up is a real lever, but only past ~Rs40,000 — below it the
    percentage applies and the rate is flat, so 'trade bigger' does nothing."""
    small = round_trip_cost_pct(5_000)
    at_cross = round_trip_cost_pct(40_000)
    large = round_trip_cost_pct(200_000)
    assert small == at_cross, "below the cap the rate is size-independent"
    assert large < small
    assert small > 0.18, "a Rs5k position really does pay ~0.19% round trip"


def test_default_reporting_size_is_what_is_actually_traded():
    """An optimistic default is how a losing strategy looks viable on paper."""
    assert round_trip_cost_pct() == round_trip_cost_pct(5_000)
    assert round_trip_cost_pct() > 0.125, "must exceed the old flat-fee figure"


def test_floor_exceeds_the_measured_gross_edge():
    """The finding the whole horizon decision rests on: even with zero brokerage
    and limit orders, the statutory floor is above the +0.030% gross edge."""
    floor = cost_floor_pct()
    assert 0.0 < floor < round_trip_cost_pct()
    assert floor > 0.030, "if this ever fails, intraday is worth revisiting"


def test_charges_are_split_across_the_correct_legs():
    """STT is sell-side only and stamp duty buy-side only, so an asymmetric
    round trip is not the same as a symmetric one of the same total turnover."""
    up = charges(100.0, 110.0, 100)     # sells the larger turnover -> more STT
    down = charges(110.0, 100.0, 100)   # buys the larger turnover -> more stamp
    assert up != down
    assert up > down, "STT (2.5bps) outweighs stamp duty (0.3bps)"


def test_charges_scale_with_quantity_and_never_go_negative():
    assert charges(100.0, 100.0, 200) > charges(100.0, 100.0, 100)
    assert charges(0.0, 0.0, 0) == 0.0
