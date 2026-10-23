"""NSE trading calendar — which dates the equity market is actually open.

Every "is the market open?" check in NeuradeX tested only the weekday, so an
exchange holiday looked like a normal trading day. Measured on 2026-09-14 (Ganesh
Chaturthi): autopilot retried a session start every minute all day and got "No
live candle data for FILATEX yet. Retry shortly." — a holiday reported as a data
outage, which misled the health check too — while the backtest walk sat idle from
09:00 to 15:40 making room for paper sessions that could never start, and the
"last completed trading day" jobs would have picked the holiday the next morning
and found nothing to process.

The dates live in nse_holidays.json beside this file. autopilot-service carries an
identical copy because the services share no code; tests/test_market_calendar.py
fails if the two ever differ, and fails when the current year is missing so a new
year is a loud reminder rather than a silent weekday-only fallback.

Weekend special sessions (Budget Sunday, Diwali Muhurat) are deliberately NOT
trading days here: they are short or symbolic, and automation should not run a
full intraday day on them.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA = json.loads(Path(__file__).with_name("nse_holidays.json").read_text(encoding="utf-8"))
NSE_TRADING_HOLIDAYS: dict[str, str] = dict(_DATA["holidays"])
COVERED_YEARS: frozenset[int] = frozenset(int(d[:4]) for d in NSE_TRADING_HOLIDAYS)
_warned_years: set[int] = set()


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def holiday_name(d) -> str | None:
    """The occasion NSE is closed for on this date, or None if it is not a listed holiday."""
    return NSE_TRADING_HOLIDAYS.get(_as_date(d).isoformat())


def is_trading_day(d) -> bool:
    """True on a weekday that is not an NSE trading holiday.

    Accepts a date, a datetime (its own calendar date is used, so pass an IST
    datetime) or a "YYYY-MM-DD" string.
    """
    day = _as_date(d)
    if day.weekday() >= 5:
        return False
    if day.year not in COVERED_YEARS and day.year not in _warned_years:
        _warned_years.add(day.year)
        logger.warning(
            "NSE holiday calendar has no data for %d — treating every weekday as a "
            "trading day. Add that year to app/utils/nse_holidays.json (and the "
            "autopilot-service copy).", day.year,
        )
    return day.isoformat() not in NSE_TRADING_HOLIDAYS


def prev_trading_day(d) -> date:
    """The most recent trading day strictly before d."""
    day = _as_date(d) - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_trading_day(d) -> date:
    """The first trading day strictly after d."""
    day = _as_date(d) + timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day
