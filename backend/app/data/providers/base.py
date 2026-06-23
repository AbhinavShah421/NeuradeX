"""Pluggable market-data provider abstraction.

Every provider knows how to fetch intraday and/or daily candles for an NSE
symbol. The registry tries them in priority order and returns the first real
result — so the platform is never tied to a single source (e.g. Groww) and
gracefully falls back to other live sources when one is rate-limited or down.

To add a new provider: subclass DataProvider, implement intraday()/daily(),
and register it in app/data/providers/__init__.py.
"""
from __future__ import annotations
from abc import ABC
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN_MIN = 9 * 60 + 15    # 09:15
SQUAREOFF_MIN   = 15 * 60 + 25   # 15:25


def candle(ts: int, o: float, h: float, l: float, c: float, v: int = 0) -> dict:
    """Build a uniform intraday candle dict (IST time label + epoch timestamp)."""
    dt = datetime.fromtimestamp(int(ts), tz=IST)
    return {
        "time": dt.strftime("%H:%M"),
        "timestamp": int(ts),
        "open": round(float(o), 2), "high": round(float(h), 2),
        "low": round(float(l), 2),  "close": round(float(c), 2),
        "volume": int(v or 0),
    }


def daily_candle(date_str: str, o: float, h: float, l: float, c: float, v: int = 0) -> dict:
    return {
        "date": date_str,
        "open": round(float(o), 2), "high": round(float(h), 2),
        "low": round(float(l), 2),  "close": round(float(c), 2),
        "volume": int(v or 0),
    }


class DataProvider(ABC):
    name: str = "base"
    requires_key: bool = False

    async def available(self) -> bool:
        """Whether this provider is configured/usable right now."""
        return True

    async def intraday(self, symbol: str, date_str: str, interval_min: int = 5) -> list[dict]:
        """Intraday candles for one trading day. [] if unsupported/no data."""
        return []

    async def daily(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        """Daily candles between start and end. [] if unsupported/no data."""
        return []
