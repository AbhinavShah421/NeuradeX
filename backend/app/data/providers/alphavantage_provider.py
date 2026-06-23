"""Alpha Vantage provider — API-key based fallback (free tier is rate-limited).

Indian equities are addressed on BSE (`SYMBOL.BSE`); prices track NSE closely.
Used as a low-priority fallback for daily/intraday history when Groww and Yahoo
are unavailable. Enabled only when ALPHA_VANTAGE_KEY is configured.
"""
from __future__ import annotations
from datetime import datetime

import httpx

from .base import DataProvider, candle, daily_candle, IST, MARKET_OPEN_MIN, SQUAREOFF_MIN
from app.config import settings
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_BASE = "https://www.alphavantage.co/query"


class AlphaVantageProvider(DataProvider):
    name = "alphavantage"
    requires_key = True

    def __init__(self) -> None:
        self.key_override: str = ""   # set from the Settings page config at runtime

    def _key(self) -> str:
        return self.key_override or getattr(settings, "ALPHA_VANTAGE_KEY", "")

    async def available(self) -> bool:
        return bool(self._key())

    async def _get(self, params: dict) -> dict | None:
        key = self._key()
        if not key:
            return None
        params["apikey"] = key
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(_BASE, params=params)
                r.raise_for_status()
                data = r.json()
            # Rate-limit / error responses come back as a Note/Information field
            if any(k in data for k in ("Note", "Information", "Error Message")):
                logger.debug("alphavantage limited: %s", str(data)[:120])
                return None
            return data
        except Exception as exc:
            logger.debug("alphavantage fetch failed: %s", exc)
            return None

    async def intraday(self, symbol: str, date_str: str, interval_min: int = 5) -> list[dict]:
        interval = f"{interval_min}min" if interval_min in (1, 5, 15, 30, 60) else "5min"
        data = await self._get({
            "function": "TIME_SERIES_INTRADAY", "symbol": f"{symbol}.BSE",
            "interval": interval, "outputsize": "full",
        })
        if not data:
            return []
        series = data.get(f"Time Series ({interval})", {})
        out: list[dict] = []
        for dt_str, bar in series.items():
            if not dt_str.startswith(date_str):
                continue
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                mins = dt.hour * 60 + dt.minute
                if MARKET_OPEN_MIN <= mins <= SQUAREOFF_MIN:
                    out.append(candle(int(dt.timestamp()), bar["1. open"], bar["2. high"],
                                      bar["3. low"], bar["4. close"], bar.get("5. volume", 0)))
            except (KeyError, ValueError):
                continue
        out.sort(key=lambda c: c["timestamp"])
        return out

    async def daily(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        data = await self._get({
            "function": "TIME_SERIES_DAILY", "symbol": f"{symbol}.BSE", "outputsize": "full",
        })
        if not data:
            return []
        series = data.get("Time Series (Daily)", {})
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        out: list[dict] = []
        for ds, bar in series.items():
            if s <= ds <= e:
                try:
                    out.append(daily_candle(ds, bar["1. open"], bar["2. high"],
                                            bar["3. low"], bar["4. close"], bar.get("5. volume", 0)))
                except KeyError:
                    continue
        out.sort(key=lambda c: c["date"])
        return out
