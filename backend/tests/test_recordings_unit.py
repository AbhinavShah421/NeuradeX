"""Unit tests for the Recordings logic (no network, no running stack).

These lock in the two guarantees that matter most: a recording always targets a
clean, not-yet-opened session (no start-gap), and its status is derived correctly
from the target date + current IST time.
"""
from datetime import datetime

from app.api import recordings as rec

IST = rec.IST


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


# ── _target_date: today while the market's open (elapsed part is backfilled) ──
# Reference weekdays: 2026-01-05 Mon, 01-06 Tue, 01-09 Fri, 01-10 Sat, 01-12 Mon.

def test_target_today_before_open():
    # Monday 08:00 IST — open hasn't passed → record today.
    assert rec._target_date(_ist(2026, 1, 5, 8, 0)) == "2026-01-05"


def test_target_mid_session_is_today():
    # Monday 10:00 — mid-session. Still today: the elapsed part (09:15→now) is
    # backfilled from history, so a late start still yields a full day.
    assert rec._target_date(_ist(2026, 1, 5, 10, 0)) == "2026-01-05"


def test_target_just_before_close_is_today():
    # 15:30 is the close cutoff — still today.
    assert rec._target_date(_ist(2026, 1, 5, 15, 30)) == "2026-01-05"


def test_target_after_close_rolls_to_next_weekday():
    # Monday 16:00 — session finished → next weekday (Tue).
    assert rec._target_date(_ist(2026, 1, 5, 16, 0)) == "2026-01-06"


def test_target_friday_evening_skips_weekend():
    # Friday 16:00 → next weekday is Monday.
    assert rec._target_date(_ist(2026, 1, 9, 16, 0)) == "2026-01-12"


def test_target_saturday_skips_to_monday():
    assert rec._target_date(_ist(2026, 1, 10, 9, 0)) == "2026-01-12"


# ── _status: derived from target date vs now ─────────────────────────────────

def test_status_future_is_scheduled():
    assert rec._status("2026-01-06", _ist(2026, 1, 5, 12, 0)) == "scheduled"


def test_status_past_is_completed():
    assert rec._status("2026-01-04", _ist(2026, 1, 5, 12, 0)) == "completed"


def test_status_today_before_open_is_scheduled():
    assert rec._status("2026-01-05", _ist(2026, 1, 5, 8, 0)) == "scheduled"


def test_status_today_during_hours_is_recording():
    assert rec._status("2026-01-05", _ist(2026, 1, 5, 11, 0)) == "recording"


def test_status_today_after_close_is_completed():
    assert rec._status("2026-01-05", _ist(2026, 1, 5, 16, 0)) == "completed"


# ── _clean_symbols: dedupe (case-insensitive), uppercase, strip, keep order ──

def test_clean_symbols_dedupes_and_normalises():
    out = rec._clean_symbols(["reliance", " tcs ", "RELIANCE", "", "Infy", "tcs"])
    assert out == ["RELIANCE", "TCS", "INFY"]


def test_clean_symbols_empty():
    assert rec._clean_symbols([]) == []
    assert rec._clean_symbols(["", "  "]) == []


# ── _view: list-shape has the keys the API/UI rely on ────────────────────────

def test_view_shape_and_status():
    now = _ist(2026, 1, 5, 12, 0)
    r = {"id": "abc123", "name": "n", "date": "2026-01-06",
         "symbols": ["A", "B"], "created_at": "c", "updated_at": "u"}
    v = rec._view(r, now)
    assert v["id"] == "abc123"
    assert v["symbol_count"] == 2
    assert v["status"] == "scheduled"
    assert "coverage_summary" in v          # cheap aggregate on the list view
    assert "coverage" not in v              # per-symbol rows only in detail view


def test_view_with_coverage_has_rows():
    now = _ist(2026, 1, 5, 12, 0)
    r = {"id": "abc123", "name": "n", "date": "2020-01-06",
         "symbols": ["NOSUCHSYM1", "NOSUCHSYM2"], "created_at": "c", "updated_at": "u"}
    v = rec._view(r, now, with_coverage=True)
    assert len(v["coverage"]) == 2
    assert v["coverage_summary"]["symbols"] == 2
    # unknown symbols → no captured ticks
    assert v["coverage_summary"]["total_ticks"] == 0
