"""Groww data provider — wraps the existing authenticated Groww client.

Strong for historical (daily + past intraday). Does not serve the in-progress
day and live quotes need a paid entitlement, so the registry falls through to
other providers for those. Calls are safe even when Groww is rate-limited: the
client's internal cooldown short-circuits without hitting the network.
"""
from __future__ import annotations
from datetime import datetime

from .base import DataProvider, candle, daily_candle, IST, MARKET_OPEN_MIN, SQUAREOFF_MIN
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


class GrowwProvider(DataProvider):
    name = "groww"

    async def available(self) -> bool:
        from app.utils.groww_client import get_groww_client
        return get_groww_client() is not None

    async def intraday(self, symbol: str, date_str: str, interval_min: int = 5) -> list[dict]:
        from app.utils.groww_client import get_groww_client
        groww = get_groww_client()
        if not groww:
            return []
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
            raw = await groww.get_historical(symbol, interval_min, day, day.replace(hour=23, minute=59))
        except Exception as exc:
            logger.debug("groww intraday %s %s failed: %s", symbol, date_str, exc)
            return []
        out: list[dict] = []
        for c in raw or []:
            if isinstance(c, list) and len(c) >= 6:
                ts = int(c[0])
                m = datetime.fromtimestamp(ts, tz=IST)
                mins = m.hour * 60 + m.minute
                if MARKET_OPEN_MIN <= mins <= SQUAREOFF_MIN:
                    out.append(candle(ts, c[1], c[2], c[3], c[4], c[5]))
        return out

    async def daily(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        from app.utils.groww_client import get_groww_client
        groww = get_groww_client()
        if not groww:
            return []
        try:
            raw = await groww.get_historical(symbol, 1440, start, end)
        except Exception as exc:
            logger.debug("groww daily %s failed: %s", symbol, exc)
            return []
        out: list[dict] = []
        for c in raw or []:
            if isinstance(c, list) and len(c) >= 6:
                ds = datetime.fromtimestamp(int(c[0])).strftime("%Y-%m-%d")
                if float(c[4]) > 0:
                    out.append(daily_candle(ds, c[1], c[2], c[3], c[4], c[5]))
        return out
