"""Unit tests for the tick-store (append/read/resample) and the day_coverage
helper the Recordings feature depends on. Uses a temp store dir via monkeypatch,
so these never touch the real dataset volume.
"""
from datetime import datetime

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
