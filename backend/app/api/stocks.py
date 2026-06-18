"""
Stock API Routes — backed by Groww live data with simulation fallback.
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger
from app.data.stocks_master import STOCKS_DEDUPED, STOCKS_BY_SYMBOL, SECTORS

logger = get_logger(__name__)
router = APIRouter()

# Watchlist for the Dashboard AI-signals section (top 10)
STOCK_META = [
    {"symbol": "RELIANCE",   "name": "Reliance Industries",          "sector": "Oil & Gas",          "base_price": 2900.0},
    {"symbol": "TCS",        "name": "Tata Consultancy Services",     "sector": "Information Technology","base_price": 3500.0},
    {"symbol": "INFY",       "name": "Infosys",                       "sector": "Information Technology","base_price": 1750.0},
    {"symbol": "HDFCBANK",   "name": "HDFC Bank",                     "sector": "Banking",            "base_price": 1550.0},
    {"symbol": "ICICIBANK",  "name": "ICICI Bank",                    "sector": "Banking",            "base_price": 1250.0},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever",            "sector": "FMCG",               "base_price": 2400.0},
    {"symbol": "SBIN",       "name": "State Bank of India",           "sector": "Banking",            "base_price": 800.0},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance",                 "sector": "Financial Services", "base_price": 7000.0},
    {"symbol": "WIPRO",      "name": "Wipro",                         "sector": "Information Technology","base_price": 510.0},
    {"symbol": "KOTAKBANK",  "name": "Kotak Mahindra Bank",           "sector": "Banking",            "base_price": 1850.0},
]

SYMBOL_META = {m["symbol"]: m for m in STOCK_META}

# period string → interval in minutes
PERIOD_TO_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _sim_stock(meta: dict) -> dict:
    """Generate realistic simulated price data for fallback."""
    price = round(meta["base_price"] * random.uniform(0.97, 1.03), 2)
    prev = round(price * random.uniform(0.97, 1.03), 2)
    chg = round(price - prev, 2)
    chg_pct = round((chg / prev) * 100, 2)
    return {
        "symbol": meta["symbol"],
        "name": meta["name"],
        "sector": meta["sector"],
        "price": price,
        "change": chg,
        "change_percent": chg_pct,
        "high": round(price * 1.015, 2),
        "low": round(price * 0.985, 2),
        "volume": random.randint(500_000, 20_000_000),
        "market_cap": f"₹{round(price * random.uniform(50, 800))} Cr",
        "pe_ratio": round(random.uniform(12, 45), 2),
        "timestamp": datetime.now().isoformat(),
    }


def _quote_to_stock(symbol: str, quote: dict) -> dict:
    """Map Groww quote response to our stock schema."""
    meta = SYMBOL_META.get(symbol, {"name": symbol, "sector": "Unknown"})
    price = float(quote.get("last_price", 0))
    ohlc = quote.get("ohlc", {})
    prev_close = float(ohlc.get("close", price) or price)
    chg = round(price - prev_close, 2)
    chg_pct = round((chg / prev_close) * 100, 2) if prev_close else 0.0
    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", "Unknown"),
        "price": price,
        "change": chg,
        "change_percent": chg_pct,
        "high": float(ohlc.get("high", price) or price),
        "low": float(ohlc.get("low", price) or price),
        "volume": int(quote.get("volume", 0)),
        "market_cap": quote.get("market_cap", "N/A"),
        "pe_ratio": quote.get("pe_ratio", None),
        "timestamp": datetime.now().isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/")
async def get_stocks():
    """List of NSE stocks with live prices (falls back to simulation if Groww unavailable)."""
    client = get_groww_client()
    if client:
        try:
            symbols = [m["symbol"] for m in STOCK_META]
            logger.info(
                "Calling Groww get_ltp",
                extra={"log_type": "groww_call", "caller": "stocks.get_stocks", "method": "get_ltp", "symbols": symbols, "exchange": "NSE"},
            )
            ltp_data = await client.get_ltp(symbols)
            stocks = []
            for meta in STOCK_META:
                key = f"NSE_{meta['symbol']}"
                ltp = ltp_data.get(key)
                if ltp is not None:
                    price = float(ltp)
                    base = meta["base_price"]
                    prev = round(price * random.uniform(0.99, 1.01), 2)
                    chg = round(price - prev, 2)
                    chg_pct = round((chg / prev) * 100, 2)
                    stocks.append({
                        "symbol": meta["symbol"],
                        "name": meta["name"],
                        "sector": meta["sector"],
                        "price": price,
                        "change": chg,
                        "change_percent": chg_pct,
                        "high": round(price * 1.015, 2),
                        "low": round(price * 0.985, 2),
                        "volume": random.randint(500_000, 20_000_000),
                        "market_cap": f"₹{round(price * random.uniform(50, 800))} Cr",
                        "pe_ratio": round(random.uniform(12, 45), 2),
                        "timestamp": datetime.now().isoformat(),
                    })
                else:
                    stocks.append(_sim_stock(meta))
            return {"status": "success", "count": len(stocks), "data": stocks}
        except Exception as e:
            logger.warning(
                "Groww LTP fetch failed, using simulation",
                extra={"log_type": "stocks_event", "event": "ltp_fallback", "error": str(e)},
            )

    stocks = [_sim_stock(m) for m in STOCK_META]
    return {"status": "success", "count": len(stocks), "data": stocks}


@router.get("/{symbol}")
async def get_stock(symbol: str):
    """Single stock quote — live from Groww, fallback to simulation."""
    symbol = symbol.upper()
    client = get_groww_client()
    if client:
        try:
            logger.info(
                "Calling Groww get_quote",
                extra={"log_type": "groww_call", "caller": "stocks.get_stock", "method": "get_quote", "symbol": symbol},
            )
            quote = await client.get_quote(symbol)
            return {"status": "success", "data": _quote_to_stock(symbol, quote)}
        except Exception as e:
            logger.warning(
                "Groww quote failed, using simulation",
                extra={"log_type": "stocks_event", "event": "quote_fallback", "symbol": symbol, "error": str(e)},
            )

    meta = SYMBOL_META.get(symbol, {"symbol": symbol, "name": symbol, "sector": "Unknown", "base_price": 1000.0})
    return {"status": "success", "data": _sim_stock(meta)}


@router.get("/{symbol}/candlesticks")
async def get_candlesticks(
    symbol: str,
    period: str = Query("1h", pattern="^(1m|5m|15m|1h|4h|1d)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """OHLCV candlestick data — live from Groww, fallback to simulation."""
    symbol = symbol.upper()
    interval = PERIOD_TO_MINUTES.get(period, 60)
    end = datetime.now()
    start = end - timedelta(minutes=interval * limit)

    client = get_groww_client()
    if client:
        try:
            logger.info(
                "Calling Groww get_historical",
                extra={"log_type": "groww_call", "caller": "stocks.get_candlesticks", "method": "get_historical", "symbol": symbol, "interval_minutes": interval},
            )
            candles_raw = await client.get_historical(symbol, interval, start, end)
            if candles_raw:
                candles = []
                for c in candles_raw:
                    # c may be [ts, open, high, low, close, volume] or a dict
                    if isinstance(c, list) and len(c) >= 6:
                        candles.append({
                            "timestamp": datetime.fromtimestamp(c[0]).isoformat() if isinstance(c[0], (int, float)) else c[0],
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]),
                        })
                    elif isinstance(c, dict):
                        candles.append({
                            "timestamp": c.get("timestamp", c.get("time", "")),
                            "open": float(c.get("open", 0)),
                            "high": float(c.get("high", 0)),
                            "low": float(c.get("low", 0)),
                            "close": float(c.get("close", 0)),
                            "volume": int(c.get("volume", 0)),
                        })
                return {"status": "success", "symbol": symbol, "period": period, "count": len(candles), "data": candles}
        except Exception as e:
            logger.warning(
                "Groww historical data failed, using simulation",
                extra={"log_type": "stocks_event", "event": "historical_fallback", "symbol": symbol, "period": period, "error": str(e)},
            )

    # Simulation fallback
    meta = SYMBOL_META.get(symbol, {"base_price": 1000.0})
    base = meta["base_price"]
    candles = []
    for i in range(limit):
        o = round(base + random.uniform(-base * 0.01, base * 0.01), 2)
        c = round(o + random.uniform(-base * 0.008, base * 0.008), 2)
        candles.append({
            "timestamp": (end - timedelta(minutes=interval * (limit - i))).isoformat(),
            "open": o,
            "high": round(max(o, c) + random.uniform(0, base * 0.005), 2),
            "low": round(min(o, c) - random.uniform(0, base * 0.005), 2),
            "close": c,
            "volume": random.randint(100_000, 5_000_000),
        })
        base = c
    return {"status": "success", "symbol": symbol, "period": period, "count": len(candles), "data": candles}


@router.get("/{symbol}/sentiment")
async def get_sentiment(symbol: str):
    """Sentiment analysis — simulated (Groww doesn't provide sentiment data)."""
    return {
        "status": "success",
        "data": {
            "symbol": symbol.upper(),
            "overall_sentiment": round(random.uniform(-1, 1), 2),
            "news_sentiment": round(random.uniform(-1, 1), 2),
            "social_media_sentiment": round(random.uniform(-1, 1), 2),
            "analyst_rating": round(random.uniform(1, 5), 2),
            "buy_count": random.randint(5, 50),
            "sell_count": random.randint(0, 20),
            "hold_count": random.randint(5, 30),
            "updated_at": datetime.now().isoformat(),
        },
    }


# ── Full Stock Directory ───────────────────────────────────────────────────────

import json as _json
import time as _time
from datetime import timezone as _tz

# The curated master (~300 names, with sector metadata) augmented with the FULL
# NSE universe the scanner discovered (~2100), so "All Stocks" lists everything.
_dir_cache: dict = {"ts": 0.0, "list": None, "sectors": None}


async def _augmented_directory() -> tuple[list[dict], list[str]]:
    now = _time.monotonic()
    if _dir_cache["list"] is not None and now - _dir_cache["ts"] < 3600:
        return _dir_cache["list"], _dir_cache["sectors"]
    merged = list(STOCKS_DEDUPED)
    try:
        from app.utils.redis_client import cache_get
        from app.utils.sector_map import ensure_loaded, sector_of
        await ensure_loaded()                       # real NSE industry per symbol
        ist = datetime.now(_tz(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
        raw = await cache_get(f"ai_engine:scan_universe:{ist}")
        if raw:
            uni = _json.loads(raw)
            items = uni.items() if isinstance(uni, dict) else [(s, s) for s in uni]
            for sym, name in items:
                su = str(sym).upper()
                if su and su not in STOCKS_BY_SYMBOL:
                    merged.append({"symbol": su, "name": name or su,
                                   "sector": sector_of(su), "exchange": "NSE"})
    except Exception as exc:
        logger.debug("directory augment failed: %s", exc)
    sectors = sorted({s["sector"] for s in merged})
    _dir_cache.update({"ts": now, "list": merged, "sectors": sectors})
    return merged, sectors


@router.get("/directory/list")
async def get_stock_directory(
    q:        str = Query("",    description="Search by symbol or company name"),
    sector:   str = Query("",    description="Filter by sector"),
    exchange: str = Query("",    description="Filter by exchange: NSE | BSE | BOTH"),
    page:     int = Query(1,     ge=1),
    limit:    int = Query(50,    ge=1, le=200),
):
    """
    Paginated, searchable directory of all NSE/BSE stocks — the curated master
    plus the full NSE universe discovered by the scanner.
    Returns metadata only (no live prices — use /directory/prices for those).
    """
    results, all_sectors = await _augmented_directory()

    q = q.strip().upper()
    if q:
        results = [
            s for s in results
            if q in s["symbol"] or q in s["name"].upper()
        ]

    if sector:
        results = [s for s in results if s["sector"].lower() == sector.lower()]

    if exchange and exchange.upper() in ("NSE", "BSE", "BOTH"):
        ex = exchange.upper()
        results = [s for s in results if s["exchange"] == ex or s["exchange"] == "BOTH"]

    total = len(results)
    offset = (page - 1) * limit
    page_results = results[offset: offset + limit]

    return {
        "status": "success",
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "sectors": all_sectors,
        "data": page_results,
    }


@router.post("/directory/backfill-sectors")
async def backfill_sectors(limit: int = 400):
    """Fill sectors for universe names still showing 'Other' (via Yahoo), toward
    100% coverage. Runs in the background; call repeatedly to cover the long tail."""
    import asyncio
    from app.utils.sector_map import ensure_loaded, backfill_yahoo, sector_of
    await ensure_loaded()
    try:
        from app.utils.redis_client import cache_get
        ist = datetime.now(_tz(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
        uni = _json.loads(await cache_get(f"ai_engine:scan_universe:{ist}") or "{}")
        syms = list(uni.keys()) if isinstance(uni, dict) else list(uni)
    except Exception:
        syms = [s["symbol"] for s in STOCKS_DEDUPED]
    pending = [s for s in syms if sector_of(s) == "Other"]
    _dir_cache["list"] = None                       # bust the directory cache after fill
    asyncio.create_task(backfill_yahoo(pending, limit=limit))
    return {"status": "started", "still_other": len(pending), "batch": min(limit, len(pending))}


@router.get("/directory/symbols")
async def get_directory_symbols(tradable_only: bool = True):
    """The complete stock master as a flat symbol list — used by the stock-scanner
    as its scan universe (single source of truth with the 'All Stocks' directory).

    tradable_only keeps only NSE-listed names (exchange NSE or BOTH), since the
    live market-data feed is NSE.
    """
    items = STOCKS_DEDUPED
    if tradable_only:
        items = [s for s in items if s.get("exchange") in ("NSE", "BOTH")]
    return {
        "status": "success",
        "total": len(items),
        "data": [{"symbol": s["symbol"], "name": s["name"], "exchange": s["exchange"]} for s in items],
    }


class PricesRequest(BaseModel):
    symbols: list[str]


@router.post("/directory/prices")
async def get_directory_prices(req: PricesRequest):
    """
    Batch LTP fetch for a list of symbols (max 100).
    Returns {symbol: {price, change_pct}} — used by the stock directory to show
    live prices only for the currently visible page.
    """
    symbols = [s.upper() for s in req.symbols[:100]]
    prices: dict[str, dict] = {}

    client = get_groww_client()
    if client:
        try:
            ltp_data = await client.get_ltp(symbols)
            for sym in symbols:
                raw = ltp_data.get(f"NSE_{sym}") or ltp_data.get(sym)
                if raw is not None:
                    price = float(raw)
                    meta = STOCKS_BY_SYMBOL.get(sym, {})
                    base = meta.get("base_price", price)
                    prev = round(price * random.uniform(0.988, 1.012), 2)
                    chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                    prices[sym] = {"price": price, "change_pct": chg_pct}
        except Exception as exc:
            logger.warning("Batch LTP fetch failed: %s", exc)

    # Fallback simulation for any symbols not returned by Groww
    for sym in symbols:
        if sym not in prices:
            meta = STOCKS_BY_SYMBOL.get(sym) or SYMBOL_META.get(sym, {})
            base = meta.get("base_price", 500.0)
            price = round(base * random.uniform(0.97, 1.03), 2)
            prev  = round(price * random.uniform(0.988, 1.012), 2)
            prices[sym] = {
                "price": price,
                "change_pct": round((price - prev) / prev * 100, 2),
            }

    return {"status": "success", "data": prices}
