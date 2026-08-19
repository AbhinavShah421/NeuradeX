"""Tests for risk-based position sizing.

The property that matters: a stop-out costs about the same in rupees whatever
the stop width. Flat 95%-of-cash sizing did not have that property, which is how
a more volatile universe enlarged losses without any decision changing.
"""
from __future__ import annotations

from app.utils.position_sizing import risk_qty, stop_pct_for

CAP = CASH = 50_000.0


# ── stop mirroring ──────────────────────────────────────────────────────────

def test_stop_matches_the_exit_formula():
    """max(1.5, 1.5*atr_pct) with atr_pct bounded to [0.5, 2.5]."""
    assert stop_pct_for(atr=1.0, price=100.0) == 1.5      # atr 1% -> 1.5, floor binds
    assert stop_pct_for(atr=2.0, price=100.0) == 3.0      # atr 2% -> 3.0
    assert stop_pct_for(atr=5.0, price=100.0) == 3.75     # atr clamped at 2.5 -> 3.75


def test_stop_falls_back_safely_on_missing_or_absurd_inputs():
    assert stop_pct_for(atr=0.0, price=100.0) == 1.5      # default atr_pct 0.8 -> floor
    assert stop_pct_for(atr=1.0, price=0.0) == 1.5
    assert stop_pct_for(atr=0.01, price=100.0) == 1.5     # atr_pct floored at 0.5


# ── the core property ───────────────────────────────────────────────────────

def test_rupee_risk_is_constant_across_stop_widths():
    """The whole point. Narrow and wide stops must cost the same to be wrong."""
    for stop in (1.5, 2.25, 3.0, 3.75):
        qty = risk_qty(CAP, CASH, fill=100.0, stop_pct=stop, risk_pct=0.01)
        loss = qty * 100.0 * stop / 100.0
        assert abs(loss - 500.0) < 100.0, f"stop {stop}% risked {loss:.0f}"


def test_a_wider_stop_buys_fewer_shares():
    narrow = risk_qty(CAP, CASH, 100.0, stop_pct=1.5)
    wide = risk_qty(CAP, CASH, 100.0, stop_pct=3.75)
    assert wide < narrow


def test_legacy_sizing_did_not_have_this_property():
    """Documents what was replaced: flat sizing risks 2.5x more on a wide stop."""
    legacy_qty = int(CASH * 0.95 / 100.0)
    narrow_loss = legacy_qty * 100.0 * 0.015
    wide_loss = legacy_qty * 100.0 * 0.0375
    assert wide_loss / narrow_loss == 2.5


# ── bounds ──────────────────────────────────────────────────────────────────

def test_never_exceeds_the_legacy_notional_cap():
    """Shrink-only: risk sizing must never buy more than 95% of cash."""
    cap = int(CASH * 0.95 / 100.0)
    for stop in (0.1, 1.5, 3.75):
        for risk in (0.01, 0.05, 0.5):
            assert risk_qty(CAP, CASH, 100.0, stop, risk_pct=risk) <= cap


def test_a_tiny_stop_is_bounded_by_notional_not_by_risk():
    """With a 0.1% stop the risk budget would allow a huge position."""
    qty = risk_qty(CAP, CASH, 100.0, stop_pct=0.1, risk_pct=0.01)
    assert qty == int(CASH * 0.95 / 100.0)


def test_at_least_one_share_is_always_returned():
    """An expensive name against a small budget still returns 1; the caller's
    cash check rejects it if unaffordable."""
    assert risk_qty(CAP, CASH, fill=45_000.0, stop_pct=3.75) == 1
    assert risk_qty(CAP, cash=10.0, fill=45_000.0, stop_pct=3.75) == 1


def test_degenerate_inputs_do_not_raise_or_go_negative():
    for kwargs in (
        dict(capital=0.0, cash=CASH, fill=100.0, stop_pct=1.5),
        dict(capital=CAP, cash=0.0, fill=100.0, stop_pct=1.5),
        dict(capital=CAP, cash=CASH, fill=0.0, stop_pct=1.5),
        dict(capital=CAP, cash=CASH, fill=100.0, stop_pct=0.0),
    ):
        assert risk_qty(**kwargs) >= 1


def test_risk_pct_scales_the_position():
    one = risk_qty(CAP, CASH, 1000.0, stop_pct=3.75, risk_pct=0.01)
    two = risk_qty(CAP, CASH, 1000.0, stop_pct=3.75, risk_pct=0.02)
    assert two == 2 * one


# ── the universe-volatility mechanism this fixes ────────────────────────────

def test_a_more_volatile_universe_no_longer_enlarges_rupee_losses():
    """Pre/post 2026-08-10 move sizes (0.569% -> 0.714%) under both regimes."""
    quiet_stop, volatile_stop = stop_pct_for(1.0, 100.0), stop_pct_for(2.0, 100.0)

    legacy = int(CASH * 0.95 / 100.0)
    legacy_ratio = (legacy * volatile_stop) / (legacy * quiet_stop)

    q = risk_qty(CAP, CASH, 100.0, quiet_stop)
    v = risk_qty(CAP, CASH, 100.0, volatile_stop)
    risk_ratio = (v * volatile_stop) / (q * quiet_stop)

    assert legacy_ratio == 2.0                    # legacy: 2x the loss
    assert abs(risk_ratio - 1.0) < 0.05           # risk sizing: same loss
