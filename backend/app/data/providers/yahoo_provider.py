"""Yahoo Finance provider — free, no API key.

Best free source for NSE: serves the in-progress day's intraday (1m/5m, ~1–2 min
delay) and long daily history. Symbol is suffixed with `.NS`.
"""
from __future__ import annotations
from datetime import datetime, timedelta

import httpx

from .base import DataProvider, candle, daily_candle, IST, MARKET_OPEN_MIN, SQUAREOFF_MIN
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def _chart(symbol: str, params: dict) -> dict | None:
    url = _BASE + f"{symbol}.NS"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.debug("yahoo fetch %s failed: %s", symbol, exc)
        return None


def _rows(data: dict):
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return [], {}
    ts = result.get("timestamp") or []
    q = (result.get("indicators", {}).get("quote") or [{}])[0]
    return ts, q


class YahooProvider(DataProvider):
    name = "yahoo"

    async def intraday(self, symbol: str, date_str: str, interval_min: int = 5) -> list[dict]:
        day = datetime.strptime(date_str, "%Y-%m-%d")
        # Yahoo expects UTC epochs; pad a day on each side and filter to the date.
        p1 = int((day - timedelta(days=1)).timestamp())
        p2 = int((day + timedelta(days=2)).timestamp())
        interval = f"{interval_min}m" if interval_min in (1, 2, 5, 15, 30, 60) else "5m"
        data = await _chart(symbol, {"period1": p1, "period2": p2, "interval": interval, "includePrePost": "false"})
        if not data:
            return []
        ts, q = _rows(data)
        o, h, l, c, v = (q.get("open", []), q.get("high", []), q.get("low", []),
                         q.get("close", []), q.get("volume", []))
        out: list[dict] = []
        for i, t in enumerate(ts):
            try:
                cl = c[i]
                if cl is None or float(cl) <= 0:
                    continue
                dt = datetime.fromtimestamp(int(t), tz=IST)
                if dt.strftime("%Y-%m-%d") != date_str:
                    continue
                mins = dt.hour * 60 + dt.minute
                if MARKET_OPEN_MIN <= mins <= SQUAREOFF_MIN:
                    out.append(candle(int(t), o[i] or cl, h[i] or cl, l[i] or cl, cl, v[i] or 0))
            except (IndexError, TypeError, ValueError):
                continue
        return out

    async def daily(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        data = await _chart(symbol, {
            "period1": int(start.timestamp()), "period2": int(end.timestamp()),
            "interval": "1d", "includePrePost": "false",
        })
        if not data:
            return []
        ts, q = _rows(data)
        o, h, l, c, v = (q.get("open", []), q.get("high", []), q.get("low", []),
                         q.get("close", []), q.get("volume", []))
        out: list[dict] = []
        for i, t in enumerate(ts):
            try:
                cl = c[i]
                if cl is None or float(cl) <= 0:
                    continue
                ds = datetime.fromtimestamp(int(t), tz=IST).strftime("%Y-%m-%d")
                out.append(daily_candle(ds, o[i] or cl, h[i] or cl, l[i] or cl, cl, v[i] or 0))
            except (IndexError, TypeError, ValueError):
                continue
        return out
