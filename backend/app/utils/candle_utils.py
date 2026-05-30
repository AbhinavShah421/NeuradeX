"""
Shared candle parsing and simulation utilities used by agent.py and backtest.py.
"""

import random
from datetime import datetime, timedelta

# Base prices shared across daily and intraday simulations
BASE_PRICES: dict[str, float] = {
    "SBIN": 820, "IDBI": 72, "SUZLON": 58, "INDUSINDBK": 870,
    "TMPV": 356, "PNB": 102, "FEDERALBNK": 182, "TMCV": 378,
    "IREDA": 178, "ZEEL": 135, "IOB": 54, "JKTYRE": 395,
    "RELIANCE": 2850, "TCS": 3450, "INFY": 1720, "HDFCBANK": 1530,
    "ICICIBANK": 1220, "BAJFINANCE": 6900, "WIPRO": 505, "KOTAKBANK": 1820,
}


def parse_candles(raw: list, date_key: str = "date") -> list[dict]:
    """
    Normalise raw Groww candle data (list-of-lists or list-of-dicts) into a
    uniform dict format.  Use date_key="timestamp" for agent.py, "date" for
    backtest.py so downstream code that reads that key is not affected.
    """
    result = []
    for c in raw:
        if isinstance(c, list) and len(c) >= 6:
            ts = c[0]
            result.append({
                date_key: datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else str(ts)[:10],
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": int(c[5]),
            })
        elif isinstance(c, dict):
            result.append({
                date_key: str(c.get("timestamp", c.get("time", "")))[:10],
                "open":   float(c.get("open", 0)),
                "high":   float(c.get("high", 0)),
                "low":    float(c.get("low", 0)),
                "close":  float(c.get("close", 0)),
                "volume": int(c.get("volume", 0)),
            })
    return [c for c in result if c["close"] > 0]


def simulate_daily_candles(
    symbol: str,
    start: datetime,
    end: datetime,
    date_key: str = "date",
    initial_factor: float = 1.0,
) -> list[dict]:
    """
    Generate synthetic daily (weekday) OHLCV candles for [start, end].

    initial_factor lets callers offset the base price — backtest uses
    random.uniform(0.60, 0.80) to simulate a historical starting point;
    agent passes 1.0 (default) for a current-price simulation.
    """
    base = BASE_PRICES.get(symbol, 500.0) * initial_factor
    result = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            o = round(base * random.uniform(0.991, 1.009), 2)
            c = round(o * random.uniform(0.993, 1.007), 2)
            result.append({
                date_key: cur.strftime("%Y-%m-%d"),
                "open":   o,
                "high":   round(max(o, c) * random.uniform(1.001, 1.012), 2),
                "low":    round(min(o, c) * random.uniform(0.988, 0.999), 2),
                "close":  c,
                "volume": random.randint(300_000, 12_000_000),
            })
            base = c
        cur += timedelta(days=1)
    return result
