"""NSE sector/industry map.

The NSE equity master (EQUITY_L.csv) has no sector column, so the full universe
(~2100 stocks) would otherwise all read "Other". NSE's index-constituent CSVs
(NIFTY Total Market ≈ 750, NIFTY 500) DO carry an "Industry" column per symbol —
we fetch those once a day, build a symbol→industry map, and cache it (Redis +
in-process) so sector_of() can label almost every traded name.

Resolution order in sector_of(): NSE industry → curated stock master → "Other".
"""
from __future__ import annotations
import csv
import io
import json
from datetime import date

import httpx

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
       "Accept": "text/csv,application/csv,*/*"}

# Broadest first so the widest coverage wins; both carry Symbol + Industry.
_URLS = [
    "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]

_map: dict[str, str] = {}
_loaded_date: str | None = None


async def ensure_loaded() -> None:
    """Populate the symbol→industry map (Redis cache, else fetch from NSE). Daily."""
    global _map, _loaded_date
    today = date.today().isoformat()
    if _map and _loaded_date == today:
        return
    key = f"ai_engine:sector_map:{today}"
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(key)
        if raw:
            _map = json.loads(raw)
            _loaded_date = today
            return
    except Exception:
        pass

    m: dict[str, str] = {}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        for url in _URLS:
            try:
                r = await client.get(url, headers=_UA)
                r.raise_for_status()
                rows = list(csv.reader(io.StringIO(r.text)))
                if not rows:
                    continue
                head = [h.strip().lower() for h in rows[0]]
                if "symbol" not in head or "industry" not in head:
                    continue
                i_sym, i_ind = head.index("symbol"), head.index("industry")
                for row in rows[1:]:
                    if len(row) <= max(i_sym, i_ind):
                        continue
                    sym = row[i_sym].strip().upper()
                    ind = row[i_ind].strip()
                    if sym and ind and sym not in m:
                        m[sym] = ind
            except Exception as exc:
                logger.warning("sector map fetch failed for %s: %s", url, exc)

    if m:
        _map = m
        _loaded_date = today
        try:
            from app.utils.redis_client import cache_set
            await cache_set(key, json.dumps(m), expire=86400 * 2)
        except Exception:
            pass
        logger.info("NSE sector map loaded: %d symbols", len(m))


def sector_of(symbol: str) -> str:
    """Industry/sector for a symbol: NSE industry → curated master → 'Other'.
    Call ensure_loaded() once (async) before relying on the NSE layer."""
    su = (symbol or "").upper()
    if su in _map:
        return _map[su]
    try:
        from app.data.stocks_master import STOCKS_BY_SYMBOL
        s = STOCKS_BY_SYMBOL.get(su)
        if s and s.get("sector"):
            return s["sector"]
    except Exception:
        pass
    return "Other"
