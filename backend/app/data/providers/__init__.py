"""Market-data provider registry — configurable at runtime.

A single entry point (`fetch_intraday`, `fetch_daily`) tries each ENABLED provider
in the user-configured priority order and returns the first real result plus the
source name. Order, enabled flags and API keys are stored in Redis and editable
from the Settings page. Adding a provider is one line in _ALL.
"""
from __future__ import annotations
import json
from datetime import datetime

from app.utils.elk_logger import get_logger
from .base import DataProvider
from .groww_provider import GrowwProvider
from .yahoo_provider import YahooProvider
from .alphavantage_provider import AlphaVantageProvider

logger = get_logger(__name__)

_CONFIG_KEY = "data_providers:config"

# All known providers, keyed by name. Default priority is this insertion order.
_ALL: dict[str, DataProvider] = {
    "groww": GrowwProvider(),
    "yahoo": YahooProvider(),
    "alphavantage": AlphaVantageProvider(),
}
_DEFAULT_ORDER = list(_ALL.keys())


# ── Config (Redis-backed) ─────────────────────────────────────────────────────

# Valid values for the "primary" provider override. "auto" = use the configured
# priority order; a specific name forces that provider to the front everywhere.
_VALID_PRIMARY = {"auto", *(_ALL.keys())}


async def get_config() -> dict:
    cfg = {"order": list(_DEFAULT_ORDER), "disabled": [], "keys": {}, "primary": "auto"}
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_CONFIG_KEY)
        if raw:
            stored = json.loads(raw)
            # keep only known providers, append any new ones at the end
            order = [n for n in stored.get("order", []) if n in _ALL]
            for n in _DEFAULT_ORDER:
                if n not in order:
                    order.append(n)
            cfg["order"] = order
            cfg["disabled"] = [n for n in stored.get("disabled", []) if n in _ALL]
            cfg["keys"] = stored.get("keys", {}) or {}
            primary = stored.get("primary", "auto")
            cfg["primary"] = primary if primary in _VALID_PRIMARY else "auto"
    except Exception as exc:
        logger.debug("provider config load failed: %s", exc)
    return cfg


async def get_primary_provider() -> str:
    """The user's forced primary data provider, or 'auto'. Read by both the
    registry and the live paper-trading path so the choice applies system-wide."""
    return (await get_config()).get("primary", "auto")


async def set_config(updates: dict) -> dict:
    cfg = await get_config()
    if "order" in updates and isinstance(updates["order"], list):
        order = [n for n in updates["order"] if n in _ALL]
        for n in _DEFAULT_ORDER:
            if n not in order:
                order.append(n)
        cfg["order"] = order
    if "disabled" in updates and isinstance(updates["disabled"], list):
        cfg["disabled"] = [n for n in updates["disabled"] if n in _ALL]
    if "keys" in updates and isinstance(updates["keys"], dict):
        cfg["keys"] = {**cfg.get("keys", {}), **updates["keys"]}
    if "primary" in updates and updates["primary"] in _VALID_PRIMARY:
        cfg["primary"] = updates["primary"]
    try:
        from app.utils.redis_client import cache_set
        await cache_set(_CONFIG_KEY, json.dumps(cfg), expire=86400 * 365)
    except Exception as exc:
        logger.warning("provider config save failed: %s", exc)
    return cfg


async def _active(cfg: dict | None = None) -> list[DataProvider]:
    cfg = cfg or await get_config()
    # Apply any runtime API-key overrides
    av_key = (cfg.get("keys") or {}).get("alphavantage")
    av = _ALL.get("alphavantage")
    if isinstance(av, AlphaVantageProvider):
        av.key_override = av_key or ""
    disabled = set(cfg.get("disabled", []))
    order = list(cfg.get("order", _DEFAULT_ORDER))
    # A forced primary provider wins: move it to the front and ignore disabled for
    # it, so an explicit choice always takes effect even if it was toggled off.
    primary = cfg.get("primary", "auto")
    if primary in _ALL:
        order = [primary] + [n for n in order if n != primary]
        disabled.discard(primary)
    return [_ALL[n] for n in order if n in _ALL and n not in disabled]


# ── Status (for the Settings page) ────────────────────────────────────────────

async def list_status() -> dict:
    cfg = await get_config()
    await _active(cfg)  # apply key overrides so availability reflects them
    disabled = set(cfg["disabled"])
    rows = []
    for name in cfg["order"]:
        p = _ALL[name]
        try:
            ok = await p.available()
        except Exception:
            ok = False
        rows.append({
            "name": name,
            "requires_key": p.requires_key,
            "available": ok,
            "enabled": name not in disabled,
            "has_key": bool((cfg["keys"] or {}).get(name)) if p.requires_key else None,
        })
    return {"providers": rows, "order": cfg["order"], "disabled": list(disabled),
            "primary": cfg.get("primary", "auto")}


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def fetch_intraday(symbol: str, date_str: str, interval_min: int = 5) -> tuple[list[dict], str]:
    """Return (candles, source). Tries each enabled provider in order; first non-empty wins."""
    symbol = symbol.upper()
    for p in await _active():
        try:
            if not await p.available():
                continue
            candles = await p.intraday(symbol, date_str, interval_min)
        except Exception as exc:
            logger.debug("provider %s intraday error: %s", p.name, exc)
            continue
        if candles:
            logger.info("Intraday candles served",
                        extra={"log_type": "data_provider", "event": "intraday",
                               "symbol": symbol, "date": date_str, "provider": p.name,
                               "count": len(candles)})
            return candles, p.name
    return [], "none"


async def fetch_daily(symbol: str, start: datetime, end: datetime) -> tuple[list[dict], str]:
    symbol = symbol.upper()
    for p in await _active():
        try:
            if not await p.available():
                continue
            candles = await p.daily(symbol, start, end)
        except Exception as exc:
            logger.debug("provider %s daily error: %s", p.name, exc)
            continue
        if candles:
            logger.info("Daily candles served",
                        extra={"log_type": "data_provider", "event": "daily",
                               "symbol": symbol, "provider": p.name, "count": len(candles)})
            return candles, p.name
    return [], "none"
