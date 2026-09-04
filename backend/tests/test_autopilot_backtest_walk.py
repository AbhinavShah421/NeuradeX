"""Regression tests for the autopilot's backtest walk.

autopilot-service has no test harness of its own, so these load its module by
path from the backend suite. That is worth the awkwardness: the walk is the
only thing that produces broad, multi-symbol decision capture, and when it
stalls the entry-selection research — the one lever the exit A/B did not close
— simply stops receiving data. It stalled for eleven days before anyone looked.

The failure, 2026-08-21 to 2026-09-01:

  1. The 09:00 IST morning cutoff calls _stop_backtest_queue, which empties
     `queue` and writes `queue_date = None` while leaving `symbols_remaining`
     populated (the day was interrupted at batch 2 of 3).
  2. That evening _do_backtest_step finds queue == [] so it skipped the
     "start the next batch" branch, which was nested inside `if queue:` and
     therefore only reachable when a batch finished by itself.
  3. It fell through to the day-complete branch, which read the date as
     `st.get("queue_date", cursor)` — and an existing key holding None never
     falls back to a default, so _prev_trading_day(None) raised.
  4. backtest_loop caught it, logged, slept 15s and retried. State was never
     persisted, so nothing changed and the crash repeated ~1,700 times a night.

Capture went from 89,797 decisions across 34 symbols (2026-08-20) to 1,830
across 5 (2026-08-21) and stayed there.
"""
import importlib.util
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parents[2]
        / "autopilot-service" / "app" / "autopilot.py")


@pytest.fixture(scope="module")
def autopilot():
    if not _SRC.exists():
        pytest.skip(f"autopilot source not present at {_SRC}")
    spec = importlib.util.spec_from_file_location("_autopilot_under_test", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prev_trading_day_tolerates_a_missing_cursor(autopilot):
    # The loop retries every BT_POLL seconds and persists nothing on the way
    # out, so ANY exception escaping _do_backtest_step is an unbounded crash
    # loop, not a one-off. This must degrade, not raise.
    assert isinstance(autopilot._prev_trading_day(None), str)
    assert isinstance(autopilot._prev_trading_day(""), str)


def test_prev_trading_day_skips_the_weekend(autopilot):
    # Monday 2026-08-24 steps back to Friday 2026-08-21, not Sunday.
    assert autopilot._prev_trading_day("2026-08-24") == "2026-08-21"
    assert autopilot._prev_trading_day("2026-08-21") == "2026-08-20"


def test_queue_date_is_never_read_with_a_fallback_default(autopilot):
    # `st.get("queue_date", cursor)` reads as safe and is not: _stop_backtest_queue
    # writes an explicit None, and dict.get only falls back when the key is
    # ABSENT. Every read must use `or` so the None is actually replaced.
    src = _SRC.read_text(encoding="utf-8")
    assert 'get("queue_date", ' not in src, (
        "queue_date read with a default — use `st.get(\"queue_date\") or ...`, "
        "the stored value is an explicit None after the morning cutoff"
    )


def test_stop_queue_leaves_the_day_resumable(autopilot):
    # _stop_backtest_queue must not clear symbols_remaining: the day is being
    # interrupted for paper trading, not abandoned. The resume path in
    # _do_backtest_step is what picks it back up.
    import inspect
    src = inspect.getsource(autopilot._stop_backtest_queue)
    assert "symbols_remaining" not in src, (
        "stopping the queue must leave the day's remaining symbols pending"
    )
