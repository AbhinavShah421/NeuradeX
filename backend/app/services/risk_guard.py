"""Daily loss breaker — the guardrail that caps the worst day.

Why a second breaker exists
---------------------------
autopilot-service already has a -5% daily limit, but it has three holes that
make it unable to stop a day like 2026-08-17 (-Rs.1,947):

  1. `_daily_pnl_pct` counts only sessions with status done/stopped, so open
     losing positions are invisible. It cannot trip until the damage is booked.
  2. It only blocks *new session starts*. Sessions already running keep trading.
  3. It stops being evaluated after the entry cutoff (~12:30), so the afternoon
     is unguarded entirely.

This one marks open positions, runs all day from the session runner, and blocks
*entries* rather than session creation. It lives in the backend because
autopilot-service is not bind-mounted (a change there needs an image rebuild,
this one needs a restart).

Deliberate design choices
-------------------------
* Sticky for the day. A recovery in open P&L does not un-trip it — that is what
  a daily stop is for. The flag expires at midnight IST.
* Two consecutive trips required. One bad tick marking 15 open positions at once
  can spike the total; requiring two evaluations ~30s apart stops a wick from
  halting the day.
* Entries only. Stops, trails, square-off and every other exit keep firing after
  a halt — halting exits would strand open risk, which is the opposite of the
  goal.
* Absolute rupees is the primary limit. The percentage leg is kept as a
  secondary because capital deployed varies with how many sessions ran.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Rs. 1,500 is ~1.6x the measured mean loss day since 2026-08-10 (-Rs.917) and
# ~3 full stops under 1% risk sizing. 0 disables the leg.
_LIMIT_ABS = float(os.getenv("NEURADEX_DAILY_LOSS_LIMIT_ABS", "1500") or 0)
_LIMIT_PCT = float(os.getenv("NEURADEX_DAILY_LOSS_LIMIT_PCT", "5.0") or 0)

_EVAL_EVERY_SECONDS = 30
_CONFIRM_TICKS = 2          # consecutive trips before the flag is set

_last_eval = 0.0
_consecutive_trips = 0
_flag_cache: tuple[float, str | None] = (0.0, None)
_FLAG_CACHE_TTL = 10.0


def _today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _flag_key(day: str | None = None) -> str:
    return f"paper:entry_halt:{day or _today_ist()}"


def _seconds_to_midnight_ist() -> int:
    now = datetime.now(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()))


def should_halt(
    total_abs: float, capital_deployed: float,
    limit_abs: float = _LIMIT_ABS, limit_pct: float = _LIMIT_PCT,
) -> tuple[bool, str]:
    """Pure decision: has the day's loss breached either limit?

    `total_abs` is realized + open, in rupees, negative when losing.
    Either limit set to 0 disables that leg; both zero disables the breaker.
    """
    if limit_abs and total_abs <= -abs(limit_abs):
        return True, (f"daily loss Rs.{total_abs:,.0f} breached the "
                      f"Rs.{abs(limit_abs):,.0f} limit")
    if limit_pct and capital_deployed > 0:
        pct = 100.0 * total_abs / capital_deployed
        if pct <= -abs(limit_pct):
            return True, (f"daily loss {pct:.2f}% breached the "
                          f"{abs(limit_pct):.2f}% limit")
    return False, ""


def summarise_sessions(running: list[dict]) -> tuple[float, float]:
    """(open_pnl, capital) over running paper sessions. Pure, for testability.

    Slim blobs carry `metrics.total_pnl`, which already includes the mark on an
    open position; `position.current_pnl` is the fallback when metrics are
    missing on a freshly-started session.
    """
    open_pnl = 0.0
    capital = 0.0
    for s in running or []:
        if (s.get("mode") or "").lower() != "paper":
            continue
        try:
            capital += float(s.get("capital") or 0.0)
        except (TypeError, ValueError):
            pass
        v = (s.get("metrics") or {}).get("total_pnl")
        if v is None:
            v = (s.get("position") or {}).get("current_pnl")
        try:
            open_pnl += float(v or 0.0)
        except (TypeError, ValueError):
            continue
    return open_pnl, capital


async def paper_day_pnl() -> dict:
    """Today's paper P&L: realized (closed sessions) + open (running)."""
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal
    from app.utils.session_store import list_running_sessions

    day = _today_ist()
    realized = 0.0
    realized_capital = 0.0
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text(
                "SELECT COALESCE(SUM(total_pnl_abs),0), COALESCE(SUM(capital),0) "
                "FROM session_metadata WHERE mode='paper' AND date=:d "
                "AND status IN ('done','stopped')"), {"d": day})).fetchone()
        if row:
            realized, realized_capital = float(row[0] or 0.0), float(row[1] or 0.0)
    except Exception:
        pass

    try:
        open_pnl, open_capital = summarise_sessions(await list_running_sessions())
    except Exception:
        open_pnl, open_capital = 0.0, 0.0

    total = realized + open_pnl
    capital = realized_capital + open_capital
    return {
        "day": day,
        "realized_abs": round(realized, 2),
        "open_abs": round(open_pnl, 2),
        "total_abs": round(total, 2),
        "capital_deployed": round(capital, 2),
        "pct": round(100.0 * total / capital, 3) if capital > 0 else 0.0,
    }


async def evaluate_and_flag(force: bool = False) -> dict | None:
    """Throttled evaluation, called from the session runner loop.

    Returns the P&L snapshot when it ran, None when throttled.
    """
    global _last_eval, _consecutive_trips

    if not (_LIMIT_ABS or _LIMIT_PCT):
        return None
    now = time.monotonic()
    if not force and (now - _last_eval) < _EVAL_EVERY_SECONDS:
        return None
    _last_eval = now

    snap = await paper_day_pnl()
    tripped, reason = should_halt(snap["total_abs"], snap["capital_deployed"])

    if not tripped:
        _consecutive_trips = 0
        return snap

    _consecutive_trips += 1
    snap["consecutive_trips"] = _consecutive_trips
    if _consecutive_trips < _CONFIRM_TICKS:
        return snap

    try:
        from app.utils.redis_client import get_redis
        await get_redis().setex(_flag_key(snap["day"]),
                                _seconds_to_midnight_ist(), reason)
        from app.utils.elk_logger import get_logger
        get_logger(__name__).warning(
            "DAILY LOSS LIMIT HIT — new paper entries halted: %s", reason,
            extra={"log_type": "risk_event", "event": "daily_loss_halt",
                   "total_abs": snap["total_abs"], "pct": snap["pct"]})
    except Exception:
        pass
    snap["halted"] = True
    return snap


async def entry_halted() -> str | None:
    """The reason entries are halted today, or None. Cached ~10s."""
    global _flag_cache
    if not (_LIMIT_ABS or _LIMIT_PCT):
        return None
    now = time.monotonic()
    ts, cached = _flag_cache
    if (now - ts) < _FLAG_CACHE_TTL:
        return cached
    try:
        from app.utils.redis_client import get_redis
        val = await get_redis().get(_flag_key())
        reason = val if isinstance(val, str) else (val.decode() if val else None)
    except Exception:
        reason = None
    _flag_cache = (now, reason)
    return reason
