"""Daily equity rollup — the scoreboard for the equity-recovery work.

Until 2026-08-19 the system had no persistent day-over-day equity series at all:
equity existed per-session, in Redis, only while a session ticked, and the
dashboard's "curve" was a cumulative per-trade pnl% indexed by trade number.
A month of −₹917/day was invisible unless someone happened to sum
session_metadata by hand — which is exactly how it went unnoticed.

This reads `session_metadata` (whose `total_pnl_abs` was always correctly
persisted) and folds it into one row per trading day with a running peak and
drawdown. `fold_daily` is pure so the arithmetic is unit-testable without a
database.

An optional provisional point for today is added from the running sessions in
Redis — marked `"provisional": true` because open positions are marked to the
last tick and the day is not finished.
"""
from __future__ import annotations

from typing import Any, Sequence

_DAILY_SQL = """
    SELECT date,
           COALESCE(SUM(total_pnl_abs), 0)  AS pnl_abs,
           COALESCE(SUM(capital), 0)        AS capital,
           COALESCE(SUM(trade_count), 0)    AS trades,
           COALESCE(SUM(win_count), 0)      AS wins,
           COUNT(*)                         AS sessions
    FROM session_metadata
    WHERE mode = :mode
      AND status IN ('done', 'stopped')
      AND date >= :since
    GROUP BY date
    ORDER BY date
"""


def fold_daily(rows: Sequence[tuple]) -> list[dict[str, Any]]:
    """(date, pnl_abs, capital, trades, wins, sessions) -> daily equity records.

    Pure: cumulative P&L, running peak, and drawdown (cum - peak, <= 0).
    Rows must already be date-ordered, as the SQL guarantees.
    """
    out: list[dict[str, Any]] = []
    cum = 0.0
    peak = 0.0
    for date_s, pnl_abs, capital, trades, wins, sessions in rows:
        pnl = float(pnl_abs or 0.0)
        cum += pnl
        peak = max(peak, cum)
        out.append({
            "date": str(date_s),
            "pnl_abs": round(pnl, 2),
            "cum_pnl_abs": round(cum, 2),
            "peak": round(peak, 2),
            "drawdown_abs": round(cum - peak, 2),
            "capital_deployed": round(float(capital or 0.0), 2),
            "trades": int(trades or 0),
            "wins": int(wins or 0),
            "sessions": int(sessions or 0),
        })
    return out


async def _provisional_today() -> dict[str, Any] | None:
    """Best-effort snapshot of today's still-running paper sessions."""
    try:
        from app.utils.session_store import list_running_sessions
        running = await list_running_sessions()
    except Exception:
        return None

    paper = [s for s in (running or []) if (s.get("mode") or "").lower() == "paper"]
    if not paper:
        return None

    open_pnl = 0.0
    for s in paper:
        m = s.get("metrics") or {}
        v = m.get("total_pnl")
        if v is None:
            v = (s.get("position") or {}).get("current_pnl")
        try:
            open_pnl += float(v or 0.0)
        except (TypeError, ValueError):
            continue
    return {"open_pnl_abs": round(open_pnl, 2), "open_sessions": len(paper),
            "provisional": True}


async def equity_daily(mode: str = "paper", days: int = 120) -> dict[str, Any]:
    """The daily equity series plus a provisional today-point."""
    from datetime import date, timedelta
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal

    since = (date.today() - timedelta(days=days)).isoformat()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text(_DAILY_SQL), {"mode": mode, "since": since})).fetchall()

    series = fold_daily(rows)
    today = await _provisional_today() if mode == "paper" else None

    last = series[-1] if series else None
    return {
        "mode": mode,
        "days": len(series),
        "series": series,
        "today_open": today,
        "summary": {
            "cum_pnl_abs": last["cum_pnl_abs"] if last else 0.0,
            "drawdown_abs": last["drawdown_abs"] if last else 0.0,
            "worst_day": min((r["pnl_abs"] for r in series), default=0.0),
            "best_day": max((r["pnl_abs"] for r in series), default=0.0),
        },
    }
