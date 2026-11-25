"""Daily collector of intraday bars for the opening gap boards. Research only.

Why: shorting the top-10 opening gap-ups at 09:20 measured +0.40%/day net
(t 1.60, 63% winning days, both halves positive) on the 35 days of 5-minute
bars Yahoo still served on 2026-11-25. Yahoo keeps 5-minute bars for only about
60 days, so without a collector the evidence can never grow past that window.
This stores, per trading day, the top-10 gap-up and gap-down boards (ranked on
open / previous close, known at 09:15) with their 5-minute bars, so the short
gap-up rule (and the long gap-down one, which failed at 09:20) can be judged on
a proper held-out sample in about six weeks.

Runs from the session runner's after-hours counterfactual sweep. Each run
catches up every missing trading day still inside Yahoo's window, so evenings
when the machine was off are filled in later.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

WINDOW_DAYS = 55          # Yahoo serves 5-minute bars for ~60 days
BOARD_N = 10
MIN_BARS = 40
_DDL = [
    """CREATE TABLE IF NOT EXISTS research_gap_bars (
        day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
        gap_pct DOUBLE PRECISION, open DOUBLE PRECISION, prev_close DOUBLE PRECISION,
        close DOUBLE PRECISION, bars JSONB, collected_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (day, symbol))""",
    """CREATE TABLE IF NOT EXISTS research_gap_days (
        day TEXT PRIMARY KEY, n_symbols INT, collected_at TIMESTAMPTZ DEFAULT now())""",
]


def boards(today: dict, history: list[dict], n: int = BOARD_N) -> dict[str, list[dict]]:
    """Top-n gap-ups and gap-downs with the same filters as the movers board
    (EQ, price >= 30, prior-session average volume >= 300k, no circuit locks)."""
    from app.research.open_movers import MIN_AVG_VOLUME, MIN_PRICE, UPPER_CIRCUIT_GAP_PCT
    rows = []
    for sym, b in today.items():
        o, pc = b["open"], b["prev_close"]
        if o < MIN_PRICE or pc <= 0:
            continue
        vols = [h[sym]["volume"] for h in history if sym in h]
        if not vols or sum(vols) / len(vols) < MIN_AVG_VOLUME:
            continue
        gap = (o / pc - 1) * 100
        if gap == 0 or abs(gap) >= UPPER_CIRCUIT_GAP_PCT:
            continue
        rows.append({"symbol": sym, "gap_pct": round(gap, 3), "open": o,
                     "prev_close": pc, "close": b.get("close")})
    ups = sorted([r for r in rows if r["gap_pct"] > 0], key=lambda r: (-r["gap_pct"], r["symbol"]))[:n]
    dns = sorted([r for r in rows if r["gap_pct"] < 0], key=lambda r: (r["gap_pct"], r["symbol"]))[:n]
    return {"up": ups, "down": dns}


def days_to_collect(have: set[str], today: date, window: int = WINDOW_DAYS) -> list[str]:
    """Weekdays inside Yahoo's window, newest first, not already collected.
    Today is excluded: the bhavcopy only exists after the close."""
    out = []
    d = today - timedelta(days=1)
    while d >= today - timedelta(days=window):
        if d.weekday() < 5 and d.isoformat() not in have:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out


async def collect_missing(max_days: int = 10) -> dict:
    """Collect up to max_days missing trading days. Safe to re-run."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.data.providers import fetch_intraday
    from app.research.open_movers import bhavcopy, _previous_sessions

    async with engine.begin() as conn:
        for ddl in _DDL:
            await conn.execute(text(ddl))
        have = {r[0] for r in (await conn.execute(text("SELECT day FROM research_gap_days"))).fetchall()}
    done = []
    for day in days_to_collect(have, date.today())[:max_days]:
        bc = await bhavcopy(day)
        if not bc:
            continue                                   # holiday or not yet published
        hist = await _previous_sessions(day, 5)
        b = boards(bc, hist)
        rows = []
        for side, picks in b.items():
            for p in picks:
                try:
                    bars, _src = await fetch_intraday(p["symbol"], day, 5)
                except Exception:
                    bars = []
                if len(bars) >= MIN_BARS:
                    rows.append({**p, "day": day, "side": side, "bars": json.dumps(bars)})
                await asyncio.sleep(0.3)
        async with engine.begin() as conn:
            for r in rows:
                await conn.execute(text("""
                    INSERT INTO research_gap_bars (day, symbol, side, gap_pct, open, prev_close, close, bars)
                    VALUES (:day, :symbol, :side, :gap_pct, :open, :prev_close, :close, CAST(:bars AS JSONB))
                    ON CONFLICT (day, symbol) DO NOTHING"""), r)
            await conn.execute(text("""
                INSERT INTO research_gap_days (day, n_symbols) VALUES (:d, :n)
                ON CONFLICT (day) DO NOTHING"""), {"d": day, "n": len(rows)})
        done.append((day, len(rows)))
    if done:
        logger.info("gap-bar collector: %s", done)
    return {"collected": done}
