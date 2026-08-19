"""Tests for the daily loss breaker.

The breaker exists because autopilot's own -5% limit cannot stop a bad day: it
counts only closed sessions, only blocks new session starts, and stops being
evaluated after ~12:30. These tests pin the properties that fix those holes —
open positions count, either limit can trip, and one bad tick cannot halt a day.
"""
from __future__ import annotations

from app.services.risk_guard import should_halt, summarise_sessions


# ── the halt decision ───────────────────────────────────────────────────────

def test_absolute_limit_trips_on_breach():
    tripped, reason = should_halt(-1500.0, 150000.0, limit_abs=1500, limit_pct=0)
    assert tripped
    assert "1,500" in reason


def test_absolute_limit_does_not_trip_just_under():
    tripped, _ = should_halt(-1499.99, 150000.0, limit_abs=1500, limit_pct=0)
    assert not tripped


def test_a_winning_day_never_trips():
    assert not should_halt(+5000.0, 150000.0, limit_abs=1500, limit_pct=5.0)[0]


def test_percentage_leg_trips_independently():
    """Small rupee loss on small deployed capital is still a big percentage."""
    tripped, reason = should_halt(-600.0, 10000.0, limit_abs=0, limit_pct=5.0)
    assert tripped
    assert "%" in reason


def test_absolute_leg_catches_what_percentage_misses():
    """The hole in a percent-only limit: 15 sessions dilute a big loser.

    -Rs.1,600 across Rs.750,000 deployed is only -0.21%, so a 5% rule sleeps
    through it. The rupee limit is the primary for exactly this reason.
    """
    assert not should_halt(-1600.0, 750000.0, limit_abs=0, limit_pct=5.0)[0]
    assert should_halt(-1600.0, 750000.0, limit_abs=1500, limit_pct=5.0)[0]


def test_both_limits_zero_disables_the_breaker():
    assert not should_halt(-999999.0, 100.0, limit_abs=0, limit_pct=0)[0]


def test_zero_capital_does_not_divide_by_zero():
    assert not should_halt(-100.0, 0.0, limit_abs=0, limit_pct=5.0)[0]


def test_limits_are_sign_insensitive():
    """A limit given as -1500 must behave like 1500, not invert the rule."""
    assert should_halt(-1500.0, 150000.0, limit_abs=-1500, limit_pct=0)[0]


# ── open + realized composition ─────────────────────────────────────────────

def _sess(mode="paper", pnl=None, current=None, capital=50000.0):
    s = {"mode": mode, "capital": capital}
    if pnl is not None:
        s["metrics"] = {"total_pnl": pnl}
    if current is not None:
        s["position"] = {"current_pnl": current}
    return s


def test_open_positions_are_counted():
    """The autopilot hole: an unrealized -Rs.2,000 must be visible."""
    open_pnl, capital = summarise_sessions([_sess(pnl=-1200.0), _sess(pnl=-800.0)])
    assert open_pnl == -2000.0
    assert capital == 100000.0


def test_position_current_pnl_is_the_fallback_before_metrics_exist():
    open_pnl, _ = summarise_sessions([_sess(pnl=None, current=-333.0)])
    assert open_pnl == -333.0


def test_non_paper_sessions_are_ignored():
    open_pnl, capital = summarise_sessions([_sess(mode="backtest", pnl=-99999.0),
                                            _sess(mode="paper", pnl=-100.0)])
    assert open_pnl == -100.0
    assert capital == 50000.0


def test_malformed_sessions_do_not_break_the_sum():
    open_pnl, _ = summarise_sessions([
        _sess(pnl=-100.0),
        {"mode": "paper"},                      # no metrics, no position
        {"mode": "paper", "metrics": {"total_pnl": "not-a-number"}},
        {},                                     # no mode at all
    ])
    assert open_pnl == -100.0


def test_empty_input_is_zero():
    assert summarise_sessions([]) == (0.0, 0.0)


# ── the composed decision ───────────────────────────────────────────────────

def test_realized_plus_open_trips_where_realized_alone_would_not():
    """The whole point: -Rs.900 booked plus -Rs.700 open is a -Rs.1,600 day."""
    realized = -900.0
    open_pnl, capital = summarise_sessions([_sess(pnl=-700.0)])
    assert not should_halt(realized, capital, limit_abs=1500, limit_pct=0)[0]
    assert should_halt(realized + open_pnl, capital, limit_abs=1500, limit_pct=0)[0]
