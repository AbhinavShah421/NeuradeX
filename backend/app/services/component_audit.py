"""Component audit — verify the output of every component on the paper decision path.

Each check inspects what one component actually produced, not whether its
process is up, because every outage NeuradeX has had was a component that kept
running while producing wrong output:
  - calendar      2026-11-24: an NSE holiday traded as a live day
  - market data   yesterday's Yahoo tape replayed under today's clock; a "live"
                  quote frozen at the prior close; a Groww socket with no ticks
  - scanner       the boot DNS race shrinking the universe (304 vs ~2,300);
                  an uncalibrated win_probability (0.82-0.95 vs 26% realised)
  - agents        a forming zero-volume candle made the anomaly agent veto ~half
                  of all decisions; an agent that errors or never varies
  - gate/runner   sessions running but no decisions being made
  - records       executor rows stored with entry_price 0

Every finding has a stable `key` so a watcher can report each fault once.
Severity: critical (decisions are being made on wrong inputs), warn (a component
is degraded), info (context worth knowing). Read-only: this never acts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

_EXPECTED_AGENTS = {"technical", "pattern", "momentum", "volatility", "sentiment"}
_ERROR_WORDS = ("error", "failed", "exception", "timeout", "unavailable", "traceback")


def _f(findings: list, key: str, component: str, severity: str, detail: str, **data) -> None:
    findings.append({"key": key, "component": component, "severity": severity,
                     "detail": detail, **({"data": data} if data else {})})


def _market_open(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= m <= 15 * 60 + 30


def _age_min(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        return (now - ts).total_seconds() / 60
    except ValueError:
        return None


async def _check_calendar(findings, now, paper):
    from app.utils.market_calendar import is_trading_day, holiday_name
    day = now.date()
    if not is_trading_day(day):
        if paper:
            _f(findings, f"calendar:closed-with-sessions:{day}", "calendar", "critical",
               f"NSE closed ({holiday_name(day) or 'weekend'}) but {len(paper)} paper sessions are running",
               sessions=[s.get("symbol") for s in paper])
        return False
    return True  # trading day


async def _check_market_data(findings, now, paper, open_now):
    from app.utils.session_store import get_session
    today = now.strftime("%Y-%m-%d")
    for slim in paper:
        sid, sym = slim.get("id"), slim.get("symbol")
        s = await get_session(sid)
        c = (s or {}).get("candles") or []
        if len(c) < 2:
            if open_now and now.hour * 60 + now.minute > 9 * 60 + 25:
                _f(findings, f"data:no-bars:{sid}", "market_data", "warn", f"{sym}: no candles yet")
            continue
        done, forming = c[-2], c[-1]
        ts = done.get("timestamp")
        bar_day = datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d") if ts else None
        if bar_day != today:
            _f(findings, f"data:stale-tape:{sid}:{today}", "market_data", "critical",
               f"{sym}: completed bars are dated {bar_day}, not today — replaying an old tape",
               session_id=sid)
            continue
        if open_now:
            lag = (now - datetime.fromtimestamp(float(ts), IST)).total_seconds() / 60
            if lag > 5:
                _f(findings, f"data:lag:{sid}:{now:%H}", "market_data", "warn",
                   f"{sym}: last completed bar is {lag:.0f} min old", session_id=sid)
        prev_close = float(done.get("close") or 0)
        live = float(forming.get("close") or 0)
        if prev_close > 0 and live > 0 and not forming.get("volume"):
            gap = abs(live / prev_close - 1) * 100
            if gap > 1.5:
                _f(findings, f"data:quote-gap:{sid}:{now:%H}", "market_data", "warn",
                   f"{sym}: live quote {live} is {gap:.1f}% off the last bar {prev_close} — "
                   f"stale or frozen quote?", session_id=sid, source=s.get("data_source"))


async def _check_groww_stream(findings, r, open_now):
    if not open_now:
        return
    n = 0
    async for _ in r.scan_iter(match="groww:ltp:*", count=500):
        n += 1
        if n > 5:
            break
    if n == 0:
        _f(findings, f"data:groww-stream-empty:{datetime.now(IST):%Y-%m-%d}", "market_data", "warn",
           "Groww websocket has published no ticks — sessions fall back to Yahoo (1-2 min lag, "
           "quote freshness unchecked)")


async def _check_scanner(findings, r, now, trading_day, open_now):
    raw = await r.get("ai_engine:watchlist")
    if not raw:
        _f(findings, "scanner:no-watchlist", "scanner", "warn", "no watchlist in Redis")
        return
    wl = json.loads(raw)
    age = _age_min(wl.get("updated_at"), now)
    if open_now and (age is None or age > 20):
        _f(findings, f"scanner:stale:{now:%Y-%m-%d-%H}", "scanner", "warn",
           f"watchlist is {age:.0f} min old" if age is not None else "watchlist has no timestamp")
    scanned = int(wl.get("scanned") or 0)
    if scanned and scanned < 1000:
        _f(findings, f"scanner:small-universe:{now:%Y-%m-%d-%H}", "scanner", "warn",
           f"scanned only {scanned} symbols (expected ~2,300) — boot DNS race fallback?")
    if not trading_day and wl.get("phase") == "intraday":
        _f(findings, f"scanner:intraday-on-holiday:{now:%Y-%m-%d}", "scanner", "warn",
           "scanner reports phase=intraday on a non-trading day — its picks are yesterday's moves")
    hot = [i.get("symbol") for i in (wl.get("items") or []) if float(i.get("win_probability") or 0) >= 0.8]
    if hot:
        _f(findings, f"scanner:uncalibrated:{now:%Y-%m-%d}", "scanner", "info",
           f"{len(hot)} picks claim win_probability >= 0.8; measured realised rate for A-grades is ~26%",
           symbols=hot)
    mv = await r.get("ai_engine:movers")
    if mv:
        m_age = _age_min(json.loads(mv).get("updated_at"), now)
        if open_now and (m_age is None or m_age > 20):
            _f(findings, f"scanner:movers-stale:{now:%Y-%m-%d-%H}", "scanner", "warn",
               f"movers list is {m_age:.0f} min old" if m_age is not None else "movers has no timestamp")


async def _check_decisions(findings, now, paper, open_now):
    """Agents + ensemble/gate, from what the sessions actually decided."""
    if not paper:
        return
    from sqlalchemy import text
    from app.database.postgres import engine
    ids = [s["id"] for s in paper]
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT session_id, symbol, candle_time, action, executed, reason, agents, created_at
            FROM session_decisions
            WHERE created_at >= :since AND session_id = ANY(:ids)
        """), {"since": since, "ids": ids})).fetchall()

    hour = f"{now:%Y-%m-%d-%H}"
    if open_now and not rows and now.hour * 60 + now.minute > 9 * 60 + 45:
        _f(findings, f"runner:no-decisions:{hour}", "runner", "critical",
           f"{len(paper)} paper sessions running but 0 decisions in 30 min — runner stalled?")
        return
    if rows:
        last = max(r[7] for r in rows)
        stall = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if open_now and stall > 5:
            _f(findings, f"runner:stalled:{hour}", "runner", "critical",
               f"newest decision is {stall:.0f} min old")

    # agents
    per: dict[str, dict[str, Any]] = {}
    veto_honored = 0
    for r in rows:
        if "veto honored" in (r[5] or ""):
            veto_honored += 1
        for a in (r[6] or []):
            name = a.get("agent")
            if not name:
                continue
            d = per.setdefault(name, {"n": 0, "acts": {}, "confs": set(), "errors": 0})
            d["n"] += 1
            d["acts"][a.get("action")] = d["acts"].get(a.get("action"), 0) + 1
            d["confs"].add(round(float(a.get("confidence") or 0), 3))
            if any(w in str(a.get("reasoning") or "").lower() for w in _ERROR_WORDS):
                d["errors"] += 1
    if rows:
        missing = _EXPECTED_AGENTS - set(per)
        if missing:
            _f(findings, f"agents:missing:{hour}", "agents", "warn",
               f"agents absent from every recent decision: {sorted(missing)}")
    for name, d in per.items():
        if d["n"] >= 20 and d["errors"] / d["n"] > 0.3:
            _f(findings, f"agents:errors:{name}:{hour}", "agents", "warn",
               f"{name}: {d['errors']}/{d['n']} votes carry an error-like reasoning")
        if d["n"] >= 40 and len(d["confs"]) == 1 and len(d["acts"]) == 1:
            _f(findings, f"agents:frozen:{name}:{hour}", "agents", "info",
               f"{name}: identical vote+confidence on all {d['n']} decisions "
               f"({next(iter(d['acts']))} @ {next(iter(d['confs']))}) — not contributing")
    if len(rows) >= 40 and veto_honored / len(rows) > 0.4:
        _f(findings, f"gate:veto-storm:{hour}", "ensemble", "warn",
           f"ensemble veto forced HOLD on {veto_honored}/{len(rows)} decisions "
           f"(the forming-candle anomaly bug looked exactly like this)")

    # gate throughput — informational only
    buys = sum(1 for r in rows if r[3] == "BUY" and r[4])
    sv_vetoes = sum(1 for r in rows if "Supervisor veto" in (r[5] or ""))
    _f(findings, f"gate:throughput:{now:%Y-%m-%d-%H}-{now.minute // 15}", "ensemble", "info",
       f"last 30 min: {len(rows)} decisions, {buys} executed BUYs, {sv_vetoes} supervisor vetoes")


async def _check_records(findings, now):
    from sqlalchemy import text
    from app.database.postgres import engine
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with engine.begin() as conn:
        phantom = (await conn.execute(text("""
            SELECT count(*) FROM trade_records
            WHERE created_at >= :s AND COALESCE(entry_price, 0) = 0
        """), {"s": start})).scalar()
    if phantom:
        _f(findings, f"records:phantom:{now:%Y-%m-%d}", "records", "warn",
           f"{phantom} trade_records today with entry_price 0 (executor/feedback field mismatch)")


async def run_audit() -> dict:
    from app.utils.redis_client import get_redis
    from app.utils.session_store import list_running_sessions
    now = datetime.now(IST)
    r = get_redis()
    findings: list[dict] = []
    paper = [s for s in await list_running_sessions() if s.get("mode") == "paper"]
    open_now = _market_open(now)
    checks = []
    trading_day = True
    try:
        trading_day = await _check_calendar(findings, now, paper)
    except Exception as exc:
        _f(findings, "audit:calendar-error", "audit", "warn", f"calendar check failed: {exc}")
    open_now = open_now and trading_day
    for name, coro in (
        ("market_data", _check_market_data(findings, now, paper, open_now)),
        ("groww", _check_groww_stream(findings, r, open_now)),
        ("scanner", _check_scanner(findings, r, now, trading_day, open_now)),
        ("decisions", _check_decisions(findings, now, paper, open_now)),
        ("records", _check_records(findings, now)),
    ):
        try:
            await coro
            checks.append(name)
        except Exception as exc:
            logger.warning("component audit %s failed", name, exc_info=True)
            _f(findings, f"audit:{name}-error:{now:%Y-%m-%d-%H}", "audit", "warn",
               f"{name} check failed: {exc}")
    order = {"critical": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda x: order.get(x["severity"], 3))
    return {"generated_at": now.isoformat(timespec="seconds"), "trading_day": trading_day,
            "market_open": open_now, "paper_sessions": len(paper),
            "open_positions": sum(1 for s in paper if (s.get("position") or {}).get("status") == "LONG"),
            "checks": checks, "findings": findings}
