"""Tests for the daily equity rollup.

Two jobs here. The first is the fold arithmetic — cumulative, peak, drawdown —
which is the instrument every later phase is measured against, so it has to be
right before anything else is trusted.

The second is a regression test for the bug that made this rollup necessary:
`_finalize_session` persisted `metrics.get("total_return_pct")`, a key
`_compute_metrics` never emits, so `session_metadata.total_pnl_pct` was 0 for
982 sessions. The test asserts the keys read at persist time are keys the
metrics function actually produces, so this class of silent-zero cannot return.
"""
from __future__ import annotations

import inspect
import re

from app.services.equity_service import fold_daily


def _row(date, pnl, capital=50000, trades=1, wins=0, sessions=1):
    return (date, pnl, capital, trades, wins, sessions)


# ── fold arithmetic ─────────────────────────────────────────────────────────

def test_empty_input_is_empty_not_an_error():
    assert fold_daily([]) == []


def test_cumulative_runs_across_days():
    out = fold_daily([_row("2026-08-01", 100), _row("2026-08-02", -30),
                      _row("2026-08-03", 50)])
    assert [r["cum_pnl_abs"] for r in out] == [100.0, 70.0, 120.0]


def test_drawdown_is_zero_at_a_new_peak():
    out = fold_daily([_row("2026-08-01", 100), _row("2026-08-02", 25)])
    assert [r["drawdown_abs"] for r in out] == [0.0, 0.0]
    assert out[-1]["peak"] == 125.0


def test_drawdown_is_measured_from_the_peak_not_from_zero():
    """Up 200, then down 300: drawdown is -300, not -100."""
    out = fold_daily([_row("2026-08-01", 200), _row("2026-08-02", -300)])
    assert out[-1]["cum_pnl_abs"] == -100.0
    assert out[-1]["peak"] == 200.0
    assert out[-1]["drawdown_abs"] == -300.0


def test_drawdown_persists_until_the_old_peak_is_exceeded():
    out = fold_daily([_row("2026-08-01", 100), _row("2026-08-02", -60),
                      _row("2026-08-03", 40), _row("2026-08-04", 30)])
    assert [r["drawdown_abs"] for r in out] == [0.0, -60.0, -20.0, 0.0]


def test_a_losing_run_from_the_start_is_all_drawdown():
    """The observed regime: peak stays 0 and every day deepens the hole."""
    out = fold_daily([_row(f"2026-08-{d:02d}", -917) for d in (10, 11, 12)])
    assert [r["peak"] for r in out] == [0.0, 0.0, 0.0]
    assert out[-1]["drawdown_abs"] == -2751.0


def test_null_pnl_is_treated_as_zero():
    out = fold_daily([_row("2026-08-01", None), _row("2026-08-02", 10)])
    assert out[0]["pnl_abs"] == 0.0
    assert out[-1]["cum_pnl_abs"] == 10.0


def test_counts_are_carried_through_as_ints():
    out = fold_daily([_row("2026-08-01", 10, capital=50000, trades=3, wins=2, sessions=4)])
    r = out[0]
    assert (r["trades"], r["wins"], r["sessions"]) == (3, 2, 4)
    assert r["capital_deployed"] == 50000.0


# ── regression: the silent-zero persistence bug ─────────────────────────────

def test_finalize_session_only_reads_metric_keys_that_are_produced():
    """`_finalize_session` must not read a metrics key `_compute_metrics` never
    emits. It read "total_return_pct" for months; the column was silently 0."""
    from app.services import sessions_service
    from app.services.backtest_service import _compute_metrics

    produced = set(_compute_metrics(50000.0, 50000.0, []).keys())

    src = inspect.getsource(sessions_service._finalize_session)
    read = set(re.findall(r'metrics\.get\(\s*["\']([A-Za-z0-9_]+)["\']', src))

    assert read, "expected _finalize_session to read metrics keys"
    missing = read - produced
    assert not missing, (
        f"_finalize_session reads metrics keys that _compute_metrics never emits: "
        f"{sorted(missing)}. Produced keys: {sorted(produced)}"
    )


def test_pnl_pct_key_specifically_is_the_emitted_one():
    """Check the call sites, not the whole source — the explanatory comment
    naming the old key is intentional documentation and must not fail this."""
    from app.services import sessions_service

    src = inspect.getsource(sessions_service._finalize_session)
    read = re.findall(r'metrics\.get\(\s*["\']([A-Za-z0-9_]+)["\']', src)

    assert "total_pnl_pct" in read
    assert "total_return_pct" not in read
