"""Tests for backtest provenance and simulator determinism.

Both fix audit findings from 2026-08-19:

  * `data_source` was set on the in-memory session and shown in the list view,
    but never persisted — 0 of 2,363 recent rows carried it. A backtest could
    run on the seeded random walk and nothing recorded that it had.
  * The simulator's seed used `hash()`, which Python randomises per process, so
    the "deterministic" fallback produced different prices on every run.
"""
from __future__ import annotations

import subprocess
import sys

from app.services.backtest_service import simulation_seed
from app.services.sessions_service import _session_provenance


# ── simulator determinism ───────────────────────────────────────────────────

def test_seed_is_stable_within_a_process():
    assert simulation_seed("SBIN", "2026-08-06") == simulation_seed("SBIN", "2026-08-06")


def test_seed_is_stable_ACROSS_processes():
    """The actual bug. hash() is salted per process; crc32 is not.

    Two fresh interpreters must agree, otherwise a backtest that falls back to
    simulation is unreproducible.
    """
    code = ("import zlib;"
            "print(zlib.crc32('SBIN2026-08-06'.encode('utf-8')) % (2**31))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True).stdout.strip() for _ in range(3)}
    assert len(runs) == 1
    assert runs.pop() == str(simulation_seed("SBIN", "2026-08-06"))


def test_different_inputs_give_different_seeds():
    assert simulation_seed("SBIN", "2026-08-06") != simulation_seed("SBIN", "2026-08-07")
    assert simulation_seed("SBIN", "2026-08-06") != simulation_seed("TCS", "2026-08-06")


def test_seed_is_in_range():
    for sym in ("SBIN", "TCS", "A", "VERYLONGSYMBOLNAME"):
        s = simulation_seed(sym, "2026-08-06")
        assert 0 <= s < 2 ** 31


# ── session provenance ──────────────────────────────────────────────────────

def _candles(times):
    return [{"time": t, "close": 100.0} for t in times]


def test_one_minute_bars_are_detected():
    s = {"data_source": "own_dataset",
         "candles": _candles(["09:15", "09:16", "09:17", "09:18"])}
    p = _session_provenance(s)
    assert p["bar_interval_min"] == 1
    assert p["data_source"] == "own_dataset"
    assert p["bars_loaded"] == 4


def test_five_minute_bars_are_detected():
    """The distinction that made backtests incomparable either side of Aug 10."""
    s = {"data_source": "yahoo",
         "candles": _candles(["09:15", "09:20", "09:25", "09:30"])}
    assert _session_provenance(s)["bar_interval_min"] == 5


def test_simulated_source_is_recorded():
    s = {"data_source": "simulated", "candles": _candles(["09:15", "09:20"])}
    p = _session_provenance(s)
    assert p["data_source"] == "simulated"
    assert p["bar_interval_min"] == 5


def test_missing_data_source_is_none_not_an_error():
    p = _session_provenance({"candles": _candles(["09:15", "09:16"])})
    assert p["data_source"] is None
    assert p["bar_interval_min"] == 1


def test_no_candles_yields_no_interval():
    p = _session_provenance({"data_source": "yahoo", "candles": []})
    assert p["bar_interval_min"] is None
    assert p["bars_loaded"] == 0


def test_malformed_times_are_skipped_without_raising():
    s = {"data_source": "yahoo",
         "candles": [{"time": "bad"}, {"time": None}, {"time": "09:15"},
                     {"time": "09:16"}]}
    assert _session_provenance(s)["bar_interval_min"] == 1


def test_a_gap_in_the_tape_does_not_inflate_the_interval():
    """Interval is the minimum positive gap, so one missing bar cannot make a
    1-minute session look like a 3-minute one."""
    s = {"data_source": "yahoo",
         "candles": _candles(["09:15", "09:18", "09:19", "09:20"])}
    assert _session_provenance(s)["bar_interval_min"] == 1


def test_provenance_keys_do_not_collide_with_metrics():
    """It is merged into the metrics dict, so a name clash would silently
    overwrite a metric."""
    from app.services.backtest_service import _compute_metrics
    metrics = set(_compute_metrics(50000.0, 50000.0, []).keys())
    prov = set(_session_provenance({"candles": []}).keys())
    assert not (metrics & prov), f"provenance would overwrite metrics: {metrics & prov}"
