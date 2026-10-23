"""Cached company-fundamentals analyst for shadow research reports.

Alpha Vantage OVERVIEW is not point-in-time data, so this report is deliberately
excluded from replay/backtests and from the intraday ensemble vote.
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import httpx

from app.config import settings
from app.utils.redis_client import cache_get, cache_set

_BASE_URL = "https://www.alphavantage.co/query"
_CACHE_TTL = 24 * 60 * 60
_CACHE_PREFIX = "ai_engine:fundamentals:"
_refreshing: set[str] = set()
_refresh_lock = asyncio.Lock()


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_overview(data: dict, provider: str = "alphavantage") -> dict | None:
    """Select bounded, typed fields; ignore API rate-limit/error payloads."""
    if not isinstance(data, dict) or not data.get("Symbol") or any(
        field in data for field in ("Note", "Information", "Error Message")
    ):
        return None
    fields = {
        "market_cap": "MarketCapitalization",
        "pe_ratio": "PERatio",
        "eps": "EPS",
        "profit_margin": "ProfitMargin",
        "return_on_equity": "ReturnOnEquityTTM",
        "debt_to_equity": "DebtToEquity",
        "revenue_growth_yoy": "QuarterlyRevenueGrowthYOY",
        "earnings_growth_yoy": "QuarterlyEarningsGrowthYOY",
    }
    metrics = {key: _number(data.get(source)) for key, source in fields.items()}
    if not any(value is not None for value in metrics.values()):
        return None
    return {
        "symbol": str(data["Symbol"]).upper()[:32],
        "name": str(data.get("Name") or "")[:120],
        "sector": str(data.get("Sector") or "")[:80],
        "currency": str(data.get("Currency") or "")[:8],
        "metrics": metrics,
        "provider": provider,
        "as_of": time.time(),
        "status": "available",
    }


def _provider_symbol(symbol: str) -> str:
    base = symbol.strip().upper().split(".", 1)[0]
    return f"{base}.BSE" if base else ""


async def refresh_fundamentals(symbol: str) -> dict | None:
    key = getattr(settings, "ALPHA_VANTAGE_KEY", "")
    provider_symbol = _provider_symbol(symbol)
    if not key or not provider_symbol:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(_BASE_URL, params={
                "function": "OVERVIEW",
                "symbol": provider_symbol,
                "apikey": key,
            })
            response.raise_for_status()
            report = normalize_overview(response.json())
        if report:
            await cache_set(_CACHE_PREFIX + symbol.strip().upper(), json.dumps(report), expire=_CACHE_TTL)
        return report
    except Exception:
        return None


async def _refresh_once(symbol: str) -> None:
    try:
        await refresh_fundamentals(symbol)
    finally:
        async with _refresh_lock:
            _refreshing.discard(symbol)


async def get_fundamentals_report(symbol: str) -> dict | None:
    """Return fresh cached data immediately; schedule at most one background refresh."""
    sym = symbol.strip().upper()
    try:
        raw = await cache_get(_CACHE_PREFIX + sym)
        if raw:
            report = json.loads(raw)
            age = time.time() - float(report.get("as_of", 0))
            if report.get("status") == "available" and 0 <= age < _CACHE_TTL:
                return report
    except Exception:
        pass

    if getattr(settings, "ALPHA_VANTAGE_KEY", ""):
        async with _refresh_lock:
            if sym not in _refreshing:
                _refreshing.add(sym)
                asyncio.create_task(_refresh_once(sym))
    return None