"""Yahoo Finance fallback data source — sync yfinance wrapped in thread executor."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

_executor = ThreadPoolExecutor(max_workers=4)


def _fetch_history_sync(symbol: str, period: str, interval: str) -> list[dict]:
    try:
        import yfinance as yf
        nse_symbol = f"{symbol}.NS"
        ticker = yf.Ticker(nse_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return []
        df = df.reset_index()
        records = []
        for _, row in df.iterrows():
            ts = row.get("Datetime") or row.get("Date")
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            records.append({
                "time": ts,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })
        return records
    except Exception:
        return []


def _fetch_quote_sync(symbol: str) -> Optional[dict]:
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.fast_info
        return {
            "ltp": float(info.last_price or 0),
            "open": float(info.open or 0),
            "high": float(info.day_high or 0),
            "low": float(info.day_low or 0),
            "volume": int(info.three_month_average_volume or 0),
        }
    except Exception:
        return None


class YahooSource:
    async def get_daily_history(self, symbol: str, days: int = 365) -> list[dict]:
        period = f"{days}d"
        return await asyncio.get_event_loop().run_in_executor(
            _executor, _fetch_history_sync, symbol, period, "1d"
        )

    async def get_intraday_history(self, symbol: str, period: str = "5d", interval: str = "5m") -> list[dict]:
        return await asyncio.get_event_loop().run_in_executor(
            _executor, _fetch_history_sync, symbol, period, interval
        )

    async def get_quote(self, symbol: str) -> Optional[dict]:
        return await asyncio.get_event_loop().run_in_executor(_executor, _fetch_quote_sync, symbol)
