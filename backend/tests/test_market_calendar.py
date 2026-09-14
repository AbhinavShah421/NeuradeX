"""NSE trading calendar.

A wrong date here is not cosmetic. A trading day marked as a holiday silently
stops paper sessions, the square-off loop and the backtest walk for that day; a
missing holiday brings back 2026-09-14, when autopilot retried a session start
every minute and reported Ganesh Chaturthi as a data outage. So these pin the
data as well as the logic.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.utils import market_calendar as mc

IST = timezone(timedelta(hours=5, minutes=30))
_AUTOPILOT_JSON = (Path(__file__).resolve().parents[2]
                   / "autopilot-service" / "app" / "nse_holidays.json")


# ── The data ────────────────────────────────────────────────────────────────

def test_ganesh_chaturthi_2026_is_closed_and_tuesday_is_open():
    assert mc.holiday_name("2026-09-14") == "Ganesh Chaturthi"
    assert not mc.is_trading_day(date(2026, 9, 14))
    assert mc.is_trading_day(date(2026, 9, 15))


def test_the_late_added_election_closure_is_present():
    # 15 Jan 2026 was added by a later NSE circular. The retail holiday pages
    # written before it omit it, so trusting one of them would replay a closed day.
    assert mc.holiday_name("2026-01-15")


def test_counts_match_the_exchange_circulars():
    by_year: dict[int, int] = {}
    for d in mc.NSE_TRADING_HOLIDAYS:
        by_year[int(d[:4])] = by_year.get(int(d[:4]), 0) + 1
    assert by_year == {2025: 14, 2026: 16}


def test_every_listed_holiday_is_a_weekday():
    # Only weekday closures belong in the table; a weekend date means a typo'd day.
    assert [d for d in mc.NSE_TRADING_HOLIDAYS if date.fromisoformat(d).weekday() >= 5] == []


def test_the_current_year_is_covered():
    # Fails on 1 January of any year nobody has added — a loud reminder instead of
    # a quiet fall back to weekday-only.
    assert datetime.now(IST).year in mc.COVERED_YEARS


def test_autopilot_carries_the_identical_calendar():
    if not _AUTOPILOT_JSON.exists():
        pytest.skip(f"autopilot source not present at {_AUTOPILOT_JSON}")
    ours = json.loads(Path(mc.__file__).with_name("nse_holidays.json").read_text(encoding="utf-8"))
    theirs = json.loads(_AUTOPILOT_JSON.read_text(encoding="utf-8"))
    assert theirs["holidays"] == ours["holidays"], (
        "backend and autopilot disagree on which days NSE is closed"
    )


# ── The logic ───────────────────────────────────────────────────────────────

def test_weekends_are_closed_and_ordinary_weekdays_open():
    assert not mc.is_trading_day(date(2026, 9, 12))   # Saturday
    assert not mc.is_trading_day(date(2026, 9, 13))   # Sunday
    assert mc.is_trading_day(date(2026, 9, 11))       # Friday


def test_accepts_datetimes_dates_and_strings():
    assert not mc.is_trading_day(datetime(2026, 9, 14, 10, 50, tzinfo=IST))
    assert not mc.is_trading_day("2026-09-14")
    assert not mc.is_trading_day(date(2026, 9, 14))


def test_stepping_over_a_holiday_and_a_weekend_together():
    # Fri 11 Sep -> Sat, Sun, holiday Mon -> Tue 15 Sep.
    assert mc.next_trading_day("2026-09-11") == date(2026, 9, 15)
    assert mc.prev_trading_day("2026-09-15") == date(2026, 9, 11)


def test_a_two_day_holiday_is_walked_through():
    # Diwali 2025: Tue 21 and Wed 22 Oct both closed.
    assert mc.prev_trading_day("2025-10-23") == date(2025, 10, 20)
    assert mc.next_trading_day("2025-10-20") == date(2025, 10, 23)


def test_an_uncovered_year_falls_back_to_weekdays_and_warns_once(caplog):
    mc._warned_years.discard(2031)
    with caplog.at_level("WARNING", logger=mc.__name__):
        assert mc.is_trading_day(date(2031, 1, 6))    # Monday
        assert mc.is_trading_day(date(2031, 1, 7))    # Tuesday
    assert sum("2031" in r.getMessage() for r in caplog.records) == 1


# ── The call sites that caused the 2026-09-14 symptoms ──────────────────────

def test_session_start_sees_a_holiday_not_an_open_market(monkeypatch):
    from app.api import paper_trading as pt
    monkeypatch.setattr(pt, "_now_ist", lambda: datetime(2026, 9, 14, 10, 50, tzinfo=IST))
    assert pt._market_status_label() == "holiday"
    assert pt._is_market_open() is False

    monkeypatch.setattr(pt, "_now_ist", lambda: datetime(2026, 9, 15, 10, 50, tzinfo=IST))
    assert pt._market_status_label() == "open"
    assert pt._is_market_open() is True


def test_the_morning_after_a_holiday_labels_the_last_real_session():
    from app.agents.counterfactual import _last_completed_trading_day
    tuesday_morning = datetime(2026, 9, 15, 8, 0, tzinfo=IST)
    assert _last_completed_trading_day(tuesday_morning) == "2026-09-11"


def test_a_holiday_evening_does_not_count_as_a_completed_session():
    from app.agents.counterfactual import _last_completed_trading_day
    holiday_evening = datetime(2026, 9, 14, 18, 0, tzinfo=IST)
    assert _last_completed_trading_day(holiday_evening) == "2026-09-11"
