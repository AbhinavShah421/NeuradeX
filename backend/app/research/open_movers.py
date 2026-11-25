"""The opening gainers board for any past trading day, rebuilt with no lookahead.

Why: the paper autopilot trades the day's top gainers first (median day range
3.64%, 26% of sessions up >=3% from the open), but 70% of labelled decisions came
from backtests of a fixed list of 27 large caps (1.77%, 8%). Gate rules were
tuned on the wrong population. On paper stocks, momentum longs are -0.358 pp vs
the same day (t -2.65); on the backtest list the same cell is +0.134. Backtests
need to replay the stocks paper actually trades.

Source: NSE's daily bhavcopy (every listed security, one ZIP per day). The board
is ranked on OPEN / PREVIOUS CLOSE, both known at 09:15. That is the board the
live scanner prices by ~09:11 from the pre-open auction (see the Nov 23
rehearsal). The liquidity filter uses the average volume of the PREVIOUS five
sessions, because that day's own volume is only known at the close.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_URL = ("https://nsearchives.nseindia.com/content/cm/"
        "BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*",
            "Referer": "https://www.nseindia.com/"}
_CACHE_KEY = "research:bhavcopy:{d}"
_CACHE_TTL = 120 * 86400
_MISS_TTL = 6 * 3600                 # holiday / not yet published

MIN_PRICE = 30.0                     # same filters as the Nov 23 rehearsal
MIN_AVG_VOLUME = 300_000
UPPER_CIRCUIT_GAP_PCT = 19.5         # a 20% gap opens locked: untradeable
_VOLUME_LOOKBACK = 5

_mem: dict[str, dict | None] = {}


async def bhavcopy(day: str) -> dict[str, dict] | None:
    """{symbol: {open, prev_close, close, volume}} for EQ-series stocks on `day`,
    or None when NSE has no file (weekend, holiday, not yet published)."""
    if day in _mem:
        return _mem[day]
    r = None
    try:
        from app.utils.redis_client import get_redis
        r = get_redis()
        raw = await r.get(_CACHE_KEY.format(d=day))
        if raw is not None:
            data = json.loads(raw) or None
            _mem[day] = data
            return data
    except Exception:
        r = None
    data = await _download(day)
    _mem[day] = data
    if r is not None:
        try:
            await r.set(_CACHE_KEY.format(d=day), json.dumps(data or {}),
                        ex=_CACHE_TTL if data else _MISS_TTL)
        except Exception:
            pass
    return data


async def _download(day: str) -> dict[str, dict] | None:
    import httpx
    ymd = day.replace("-", "")
    try:
        async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
            resp = await c.get(_URL.format(ymd=ymd))
        if resp.status_code != 200 or not resp.content.startswith(b"PK"):
            return None
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        rows = csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8"))
        return parse_rows(rows)
    except Exception:
        logger.warning("bhavcopy download failed for %s", day, exc_info=True)
        return None


def parse_rows(rows) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        if row.get("SctySrs") != "EQ":
            continue
        try:
            out[row["TckrSymb"].upper()] = {
                "open": float(row["OpnPric"]), "prev_close": float(row["PrvsClsgPric"]),
                "close": float(row["ClsPric"]), "volume": float(row["TtlTradgVol"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def _previous_sessions(day: str, k: int) -> list[dict]:
    """Bhavcopies of the k trading sessions before `day` (walks back past holidays)."""
    out: list[dict] = []
    d = date.fromisoformat(day)
    for _ in range(k * 3 + 6):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        bc = await bhavcopy(d.isoformat())
        if bc:
            out.append(bc)
            if len(out) == k:
                break
    return out


def rank_board(today: dict[str, dict], history: list[dict], n: int) -> list[dict]:
    """Pure ranking: the top `n` positive opening gaps that pass the filters."""
    board = []
    for sym, b in today.items():
        o, pc = b["open"], b["prev_close"]
        if o < MIN_PRICE or pc <= 0:
            continue
        vols = [h[sym]["volume"] for h in history if sym in h]
        if not vols or sum(vols) / len(vols) < MIN_AVG_VOLUME:
            continue
        gap = (o / pc - 1) * 100
        if gap <= 0 or gap >= UPPER_CIRCUIT_GAP_PCT:
            continue
        board.append({"symbol": sym, "gap_pct": round(gap, 2), "open": o,
                      "prev_close": pc, "avg_volume": int(sum(vols) / len(vols))})
    board.sort(key=lambda x: (-x["gap_pct"], x["symbol"]))
    return board[:n]


# Yahoo serves 1-minute bars for about the last 30 days only; older days have
# bars only for symbols the live stream captured into the tick store. A mover
# with no bars cannot be backtested, so the board for an old day keeps only the
# movers that have bars. (Measured 2026-11-25: SHALPAINTS 2026-11-23 371 bars,
# 2026-10-20 none.)
_PROVIDER_WINDOW_DAYS = 25
_MIN_DAY_TICKS = 1000


def _has_bars(sym: str, day: str) -> bool:
    if (date.today() - date.fromisoformat(day)).days <= _PROVIDER_WINDOW_DAYS:
        return True
    try:
        from app.data.candle_store import day_coverage
        return day_coverage(sym, day).get("ticks", 0) >= _MIN_DAY_TICKS
    except Exception:
        return False


async def open_movers(day: str, n: int = 15, require_bars: bool = True) -> dict:
    """The opening gainers board for `day` as it stood at 09:15. With
    require_bars, movers that have no minute bars for that day are skipped
    (the board then reaches further down the ranking)."""
    import asyncio
    today = await bhavcopy(day)
    if not today:
        return {"date": day, "available": False, "movers": []}
    history = await _previous_sessions(day, _VOLUME_LOOKBACK)
    ranked = rank_board(today, history, len(today))
    if not require_bars:
        return {"date": day, "available": True, "sessions_for_volume": len(history),
                "movers": ranked[:n]}
    kept, skipped = [], 0
    for m in ranked:
        if await asyncio.to_thread(_has_bars, m["symbol"], day):
            kept.append(m)
            if len(kept) == n:
                break
        else:
            skipped += 1
    return {"date": day, "available": True, "sessions_for_volume": len(history),
            "skipped_no_bars": skipped, "movers": kept}
