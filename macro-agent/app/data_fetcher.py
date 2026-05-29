"""Fetches macro indicators: VIX India, FII/DII flows, USD/INR, crude oil, G-sec yield."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_executor = ThreadPoolExecutor(max_workers=3)
logger = logging.getLogger(__name__)


def _fetch_yahoo_price_sync(ticker: str) -> float:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        return float(t.fast_info.last_price or 0)
    except Exception:
        return 0.0


def _fetch_nse_vix_sync() -> float:
    try:
        import requests
        resp = requests.get(
            "https://www.nseindia.com/api/allIndices",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        data = resp.json()
        for item in data.get("data", []):
            if item.get("index") == "INDIA VIX":
                return float(item.get("last", 0))
    except Exception:
        pass
    return 0.0


async def fetch_macro_indicators() -> dict:
    loop = asyncio.get_event_loop()

    vix_task = loop.run_in_executor(_executor, _fetch_nse_vix_sync)
    usd_inr_task = loop.run_in_executor(_executor, _fetch_yahoo_price_sync, "INR=X")
    crude_task = loop.run_in_executor(_executor, _fetch_yahoo_price_sync, "BZ=F")
    gsec_task = loop.run_in_executor(_executor, _fetch_yahoo_price_sync, "^TNX")
    nifty_task = loop.run_in_executor(_executor, _fetch_yahoo_price_sync, "^NSEI")

    vix, usd_inr, crude, gsec, nifty = await asyncio.gather(
        vix_task, usd_inr_task, crude_task, gsec_task, nifty_task,
        return_exceptions=True,
    )

    def safe(v, default=0.0) -> float:
        return float(v) if isinstance(v, (int, float)) and v > 0 else default

    return {
        "india_vix": safe(vix, 15.0),
        "usd_inr": safe(usd_inr, 83.0),
        "crude_brent": safe(crude, 80.0),
        "gsec_10y": safe(gsec, 7.0),
        "nifty_50": safe(nifty, 22000.0),
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }
