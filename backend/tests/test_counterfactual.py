"""Unit tests for the counterfactual labeler (no network, no DB).

The simulator must mirror the live LONG policy: entry at next-bar open with
buy-side slippage, ATR-scaled stop/target/trail exits via _tech_signal, hold-cap
and 14:45 square-off, real round-trip costs.
"""
from app.agents.counterfactual import _simulate_long, _bar_index_for_time


def _bar(hhmm: str, o: float, h: float, l: float, c: float, vol: int = 0) -> dict:
    return {"time": hhmm, "open": o, "high": h, "low": l, "close": c, "volume": vol}


def _flat_day(start_h=10, start_m=0, n=60, px=100.0) -> list[dict]:
    """n one-minute bars of a perfectly flat market."""
    bars = []
    for i in range(n):
        m = start_m + i
        bars.append(_bar(f"{start_h + m // 60:02d}:{m % 60:02d}", px, px, px, px))
    return bars


# ── _bar_index_for_time ───────────────────────────────────────────────────────

def test_bar_index_found_and_missing():
    bars = _flat_day(n=5)
    assert _bar_index_for_time(bars, "10:03") == 3
    assert _bar_index_for_time(bars, "11:59") is None


# ── _simulate_long ────────────────────────────────────────────────────────────

def test_flat_market_loses_only_costs():
    # No price movement → the only pnl is slippage + charges: small and negative.
    bars = _flat_day(n=45)
    pnl = _simulate_long(bars, entry_idx=1, max_hold_minutes=30)
    assert pnl is not None
    assert -1.0 < pnl < 0.0


def test_winner_is_positive_and_capped_by_take_profit():
    # Steady climb: +0.2%/bar. The ATR-scaled take-profit (≥ +2.5%) should fire
    # and the result must be clearly positive after costs.
    bars = []
    px = 100.0
    for i in range(60):
        o = px
        px = round(px * 1.002, 4)
        bars.append(_bar(f"10:{i:02d}" if i < 60 else "11:00", o, px, o, px))
    pnl = _simulate_long(bars, entry_idx=1, max_hold_minutes=59)
    assert pnl is not None
    assert pnl > 1.0


def test_loser_is_stopped_out_not_ridden_to_zero():
    # Steady fall: -0.4%/bar. The stop (~ -1%) must cut it long before -10%.
    bars = []
    px = 100.0
    for i in range(40):
        o = px
        px = round(px * 0.996, 4)
        bars.append(_bar(f"10:{i:02d}", o, o, px, px))
    pnl = _simulate_long(bars, entry_idx=1, max_hold_minutes=39)
    assert pnl is not None
    assert -4.0 < pnl < -0.5      # stopped early, not -15%


def test_hold_cap_exits_even_in_quiet_market():
    # 10-minute cap in a flat market → exits at the cap, tiny cost-only loss.
    bars = _flat_day(n=30)
    pnl = _simulate_long(bars, entry_idx=2, max_hold_minutes=10)
    assert pnl is not None
    assert -1.0 < pnl < 0.0


def test_squareoff_forces_exit_before_close():
    # Entry at 14:40 with a huge hold cap must still exit by ~14:45.
    bars = []
    for i in range(30):
        m = 40 + i
        bars.append(_bar(f"14:{m:02d}" if m < 60 else f"15:{m-60:02d}", 100, 100, 100, 100))
    pnl = _simulate_long(bars, entry_idx=0, max_hold_minutes=500)
    assert pnl is not None            # exited (square-off), didn't run off the end


def test_entry_past_end_of_day_is_unlabelable():
    bars = _flat_day(n=5)
    assert _simulate_long(bars, entry_idx=5) is None
    assert _simulate_long(bars, entry_idx=99) is None
