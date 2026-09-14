"""Unit tests for the tick-store (append/read/resample) and the day_coverage
helper the Recordings feature depends on. Uses a temp store dir via monkeypatch,
so these never touch the real dataset volume.
"""
from datetime import datetime

import os

import pytest

from app.data import candle_store as cs

DATE = "2026-01-05"


@pytest.fixture
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_TICKS", str(tmp_path / "ticks"))
    monkeypatch.setattr(cs, "_VOL", str(tmp_path / "volume"))
    return tmp_path


def _ep(h, m, s=0):
    return int(datetime(2026, 1, 5, h, m, s, tzinfo=cs.IST).timestamp())


def test_day_coverage_empty(tmp_store):
    cov = cs.day_coverage("RELIANCE", DATE)
    assert cov["ticks"] == 0
    assert cov["first_ts"] is None and cov["last_ts"] is None
    assert cov["full_day"] is False and cov["start_clean"] is False


def test_append_read_and_full_day(tmp_store):
    ticks = [(_ep(9, 15), 100.0), (_ep(9, 16), 101.0), (_ep(12, 0), 102.5), (_ep(15, 29), 99.0)]
    written = cs.append_ticks("RELIANCE", ticks)
    assert written == 4

    cov = cs.day_coverage("RELIANCE", DATE)
    assert cov["ticks"] == 4
    assert cov["first_time"] == "09:15:00"
    assert cov["last_time"] == "15:29:00"
    assert cov["start_clean"] is True
    assert cov["end_clean"] is True
    assert cov["full_day"] is True

    # Resample to 1-minute OHLC bars.
    bars = cs.read_bars("RELIANCE", DATE, 60)
    assert len(bars) == 4                      # four distinct minutes
    assert bars[0]["open"] == 100.0
    assert bars[0]["time"] == "09:15"
    assert bars[-1]["close"] == 99.0


def test_append_dedupes_on_second(tmp_store):
    # Two prices in the same epoch-second → last one wins, one row kept.
    cs.append_ticks("TCS", [(_ep(10, 0), 50.0)])
    cs.append_ticks("TCS", [(_ep(10, 0), 55.0)])
    cov = cs.day_coverage("TCS", DATE)
    assert cov["ticks"] == 1
    bars = cs.read_bars("TCS", DATE, 60)
    assert bars[0]["close"] == 55.0


def test_day_coverage_partial(tmp_store):
    # First tick at 11:00 (well after the open) → not a clean full day.
    cs.append_ticks("INFY", [(_ep(11, 0), 100.0), (_ep(15, 29), 101.0)])
    cov = cs.day_coverage("INFY", DATE)
    assert cov["ticks"] == 2
    assert cov["first_time"] == "11:00:00"
    assert cov["start_clean"] is False
    assert cov["full_day"] is False


def test_resample_sub_minute_bars(tmp_store):
    cs.append_ticks("RELIANCE", [(_ep(9, 15, 0), 100.0), (_ep(9, 15, 5), 101.0), (_ep(9, 15, 12), 99.5)])
    bars = cs.read_bars("RELIANCE", DATE, 5)      # 5-second bars
    assert len(bars) == 3
    assert bars[0]["time"] == "09:15:00"          # sub-minute format includes seconds


# ── coverage(): footer counts, cached, never read twice ─────────────────────
# Added 2026-09-14. coverage() loaded every tick file with read_parquet to take
# len() of it and coverage_summary() walked the store again — 43.9 s per request
# on a single-worker async backend, which froze the whole API and made the
# Pattern Memory dataset panel (15 s client timeout) fail every time.

import pandas as _pd
import pyarrow.parquet as _pq


def _write_day(root, symbol, day, n_rows):
    d = root / "ticks" / symbol
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{day}.parquet"
    _pd.DataFrame({"ts": list(range(n_rows)), "price": [100.0] * n_rows}).to_parquet(path)
    return path


def test_coverage_counts_rows_and_bytes_per_symbol_day(tmp_store):
    cs._row_count_cache.clear()
    a = _write_day(tmp_store, "RELIANCE", "2026-09-10", 3)
    b = _write_day(tmp_store, "TCS", "2026-09-10", 5)

    rows = cs.coverage()

    assert [(r["symbol"], r["date"], r["ticks"]) for r in rows] == [
        ("RELIANCE", "2026-09-10", 3), ("TCS", "2026-09-10", 5)]
    assert rows[0]["bytes"] == a.stat().st_size
    assert rows[1]["bytes"] == b.stat().st_size


def test_summary_is_built_from_rows_passed_in_without_a_second_walk(tmp_store, monkeypatch):
    cs._row_count_cache.clear()
    _write_day(tmp_store, "RELIANCE", "2026-09-10", 3)
    _write_day(tmp_store, "RELIANCE", "2026-09-11", 4)
    _write_day(tmp_store, "TCS", "2026-09-10", 5)
    rows = cs.coverage()

    def walked_again():
        raise AssertionError("coverage_summary(rows) must not re-walk the store")

    monkeypatch.setattr(cs, "coverage", walked_again)
    summary = cs.coverage_summary(rows)

    assert summary == {"symbols": 2, "days": 3, "total_ticks": 12,
                       "total_bytes": sum(r["bytes"] for r in rows)}


def test_summary_without_rows_still_works_for_existing_callers(tmp_store):
    cs._row_count_cache.clear()
    _write_day(tmp_store, "INFY", "2026-09-10", 6)

    assert cs.coverage_summary()["total_ticks"] == 6


def test_unchanged_files_are_served_from_the_cache(tmp_store, monkeypatch):
    """A finished day never changes, so a repeat call must not reopen it."""
    cs._row_count_cache.clear()
    _write_day(tmp_store, "RELIANCE", "2026-09-10", 3)
    assert cs.coverage()[0]["ticks"] == 3

    def reopened(*a, **k):
        raise AssertionError("an unchanged file was reopened")

    monkeypatch.setattr(_pq, "ParquetFile", reopened)
    assert cs.coverage()[0]["ticks"] == 3


def test_a_growing_file_is_recounted(tmp_store):
    """Today's file grows all session; a stale cached count would under-report it."""
    cs._row_count_cache.clear()
    path = _write_day(tmp_store, "RELIANCE", "2026-09-14", 3)
    assert cs.coverage()[0]["ticks"] == 3

    _pd.DataFrame({"ts": list(range(9)), "price": [101.0] * 9}).to_parquet(path)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert cs.coverage()[0]["ticks"] == 9


def test_corrupt_and_in_flight_files_are_skipped_not_counted_as_zero(tmp_store):
    cs._row_count_cache.clear()
    _write_day(tmp_store, "RELIANCE", "2026-09-10", 3)
    d = tmp_store / "ticks" / "RELIANCE"
    (d / "2026-09-11.parquet").write_bytes(b"not a parquet file")
    (d / "2026-09-12.parquet.tmp.4242").write_bytes(b"half written")

    rows = cs.coverage()

    assert [r["date"] for r in rows] == ["2026-09-10"]
