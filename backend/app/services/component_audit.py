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
                # A gap is only suspicious if the independent Groww stream
                # disagrees: on 2026-11-25 JWL's quote was 2.0% above its last
                # bar because the stock was climbing (Groww 203.11 vs session
                # 201.81) — a gainers-first board moves like that all day.
                try:
                    from app.utils import groww_feed
                    g = await groww_feed.get_ltp(sym)
                except Exception:
                    g = 0.0
                if g and abs(live / g - 1) * 100 < 1.0:
                    continue
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
    # The storm check is for a BROKEN veto (the forming-candle anomaly bug).
    # The memory veto blocks 20-56% of paper decisions by design, and its
    # outcome effect was measured null (Nov 24 veto audit), so it is reported
    # but not counted: on Nov 25 it was 162 of 182 vetoes and kept this warning
    # on every round.
    veto_honored = veto_memory = 0
    for r in rows:
        if "veto honored" in (r[5] or ""):
            if "memory veto" in (r[5] or ""):
                veto_memory += 1
            else:
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
           f"non-memory ensemble veto forced HOLD on {veto_honored}/{len(rows)} decisions "
           f"(the forming-candle anomaly bug looked exactly like this)")
    if len(rows) >= 40 and veto_memory / len(rows) > 0.6:
        _f(findings, f"gate:memory-veto-high:{hour}", "ensemble", "info",
           f"memory veto forced HOLD on {veto_memory}/{len(rows)} decisions "
           f"(above its measured 20-56% range)")

    # gate throughput — informational only
    buys = sum(1 for r in rows if r[3] == "BUY" and r[4])
    sv_vetoes = sum(1 for r in rows if "Supervisor veto" in (r[5] or ""))
    _f(findings, f"gate:throughput:{now:%Y-%m-%d-%H}-{now.minute // 15}", "ensemble", "info",
       f"last 30 min: {len(rows)} decisions, {buys} executed BUYs, {sv_vetoes} supervisor vetoes")


async def _check_records(findings, now):
    from sqlalchemy import text
    from app.database.postgres import engine
    # A rolling 2 h window, not the whole day: a fault fixed at noon must stop
    # reading as current by the afternoon.
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    async with engine.begin() as conn:
        phantom = (await conn.execute(text("""
            SELECT count(*) FROM trade_records
            WHERE created_at >= :s AND COALESCE(entry_price, 0) = 0
        """), {"s": start})).scalar()
    if phantom:
        _f(findings, f"records:phantom:{now:%Y-%m-%d}", "records", "warn",
           f"{phantom} trade_records in the last 2h with entry_price 0 (executor/feedback field mismatch)")


_TRAINING_SANITY_SQL = """
    SELECT trade_source,
           count(*)                                                   AS n,
           count(*) FILTER (WHERE outcome = 'WIN')                    AS wins,
           avg(pnl_pct)                                               AS avg_pct,
           sum(pnl_abs)                                               AS pnl,
           count(*) FILTER (WHERE COALESCE(entry_price,0) <= 0
                              OR (timestamp_close IS NOT NULL
                                  AND COALESCE(exit_price,0) <= 0))   AS bad_price,
           count(*) FILTER (WHERE abs(pnl_pct) > 0.20)                AS absurd_pnl,
           count(*) FILTER (WHERE to_char(timestamp_open AT TIME ZONE 'Asia/Kolkata',
                                          'HH24:MI') >= '14:45')      AS late_entry,
           count(*) FILTER (WHERE COALESCE(market_context->>'exit_reason','unknown')
                              = 'unknown')                            AS no_exit_reason,
           count(*) FILTER (WHERE duration_minutes < 0)               AS negative_hold
    FROM trade_records
    WHERE created_at >= :since AND trade_source IN ('PAPER','REPLAY','BACKTEST')
    GROUP BY trade_source
"""


async def _training_rows(since: datetime) -> list[dict]:
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        rows = (await conn.execute(text(_TRAINING_SANITY_SQL), {"since": since})).mappings().all()
    return [dict(r) for r in rows]


async def _check_training_data(findings, now):
    """Trades the learning loops will consume must be physically possible."""
    since = datetime.now(timezone.utc) - timedelta(hours=2)
    for r in await _training_rows(since):
        src = r["trade_source"]
        for col, what in (("bad_price", "a zero/negative entry or exit price"),
                          ("absurd_pnl", "|P&L| above 20% on an intraday trade"),
                          ("late_entry", "an entry at or after 14:45"),
                          ("negative_hold", "a negative holding time")):
            if r[col]:
                _f(findings, f"training:{col}:{src}:{now:%Y-%m-%d-%H}", "training_data", "warn",
                   f"{r[col]} of {r['n']} {src} trades in the last 2h have {what} — "
                   f"these feed the learning loops")


async def activity(minutes: int = 15) -> dict:
    """What happened in the last `minutes`, per mode — the supervisor's round view."""
    from app.services import supervisor
    from app.utils.session_store import list_running_sessions
    now = datetime.now(IST)
    cutoff = (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    events = [e for e in await supervisor.get_log(2000) if e.get("ts", "") >= cutoff]
    per: dict[str, dict] = {}
    for e in events:
        m = e.get("mode") or "paper"
        d = per.setdefault(m, {"entries": 0, "exits": 0, "vetoes": 0, "pnl": 0.0,
                               "wins": 0, "exit_triggers": {}, "veto_reasons": {}})
        ev = e.get("event")
        if ev == "entry_allowed":
            d["entries"] += 1
        elif ev == "exit":
            d["exits"] += 1
            d["pnl"] = round(d["pnl"] + float(e.get("pnl") or 0), 2)
            d["wins"] += 1 if float(e.get("pnl") or 0) > 0 else 0
            t = e.get("trigger") or "unknown"
            d["exit_triggers"][t] = d["exit_triggers"].get(t, 0) + 1
        elif ev == "veto":
            d["vetoes"] += 1
            r = str(e.get("reason") or "")[:60]
            d["veto_reasons"][r] = d["veto_reasons"].get(r, 0) + 1
    running: dict[str, int] = {}
    progress: dict[str, list] = {}
    for s in await list_running_sessions():
        m = s.get("mode") or "?"
        running[m] = running.get(m, 0) + 1
        progress.setdefault(m, []).append(s.get("current_time") or "")
    since_utc = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    try:
        records = await _training_rows(since_utc)
        for r in records:
            for k in ("avg_pct", "pnl"):
                r[k] = round(float(r[k] or 0), 4 if k == "avg_pct" else 2)
    except Exception as exc:
        records = [{"error": str(exc)}]
    return {
        "generated_at": now.isoformat(timespec="seconds"), "window_min": minutes,
        "running": running,
        "progress": {m: {"min": min(v), "max": max(v)} for m, v in progress.items() if v},
        "supervisor": per, "trade_records": records,
    }


async def _check_infra(findings, now):
    """Containers, databases and nightly learning loops — the System Map's own
    probes, so this checklist and that page never disagree."""
    from app.api.monitor import snapshot
    snap = await snapshot(force=True)
    if not snap.get("faults") and not snap.get("loops"):
        _f(findings, f"infra:no-snapshot:{now:%Y-%m-%d-%H}", "infra", "warn",
           "System Map snapshot returned nothing")
        return
    for f in snap.get("faults") or []:
        sev = {"critical": "critical", "warning": "warn"}.get(f.get("severity"), "info")
        _f(findings, f"infra:{f.get('kind')}:{f.get('target')}:{now:%Y-%m-%d}", "infra", sev,
           f"{f.get('target')}: {f.get('message')}")
    for lp in snap.get("loops") or []:
        if lp.get("status") not in ("ok", None):
            _f(findings, f"loops:{lp.get('id')}:{lp.get('status')}:{now:%Y-%m-%d}", "learning_loops",
               "warn", f"learning loop {lp.get('id')} is {lp.get('status')}"
                       + (f": {lp.get('last_error')}" if lp.get("last_error") else ""))


async def _check_autopilot(findings, r, now, trading_day):
    """Autopilot disarmed itself once via a 30-day Redis TTL; check it is armed."""
    paper_on = await r.get("ai_engine:autopilot_enabled")
    m = now.hour * 60 + now.minute
    if trading_day and 8 * 60 + 30 <= m <= 15 * 60 and str(paper_on) not in ("1", "true", "True"):
        _f(findings, f"autopilot:paper-off:{now:%Y-%m-%d}", "autopilot", "warn",
           "paper autopilot is OFF on a trading day — no paper sessions will start")
    ttl = await r.ttl("ai_engine:autopilot_enabled")
    if paper_on and ttl is not None and 0 < ttl < 3 * 86400:
        _f(findings, f"autopilot:ttl:{now:%Y-%m-%d}", "autopilot", "warn",
           f"paper autopilot flag expires in {ttl / 3600:.0f} h (Redis TTL)")


async def _check_runners(findings, now):
    """Every running session, any mode, must keep producing decisions."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.session_store import list_running_sessions
    by_mode: dict[str, list[str]] = {}
    for s in await list_running_sessions():
        # A session that started moments ago has not decided yet — not a stall.
        age = _age_min(s.get("created_at"), now)
        if age is not None and age < 10:
            continue
        by_mode.setdefault(s.get("mode") or "?", []).append(s["id"])
    open_now = _market_open(now)
    for mode, ids in by_mode.items():
        if mode == "paper" and not open_now:
            continue
        async with engine.begin() as conn:
            last = (await conn.execute(text(
                "SELECT max(created_at) FROM session_decisions WHERE session_id = ANY(:ids)"),
                {"ids": ids})).scalar()
        age = None if last is None else (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age is None or age > 10:
            _f(findings, f"runner:{mode}-stalled:{now:%Y-%m-%d-%H}", "runner", "critical",
               f"{len(ids)} {mode} sessions running but the newest decision is "
               + ("missing" if age is None else f"{age:.0f} min old"))


async def _check_labels(findings, now):
    """Counterfactual labels are what every learning loop trains on. On
    2026-11-24 they had been NULL for 20 days while the loop reported ok."""
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        r = (await conn.execute(text("""
            SELECT count(*) FILTER (WHERE cf_labeled_at > now() - interval '24 hours')        AS lab,
                   count(*) FILTER (WHERE cf_labeled_at > now() - interval '24 hours'
                                      AND cf_pnl_pct IS NOT NULL)                           AS good,
                   count(*) FILTER (WHERE cf_labeled_at IS NULL
                                      AND created_at < now() - interval '30 hours'
                                      AND created_at > now() - interval '6 days')           AS stuck
            FROM session_decisions WHERE created_at > now() - interval '7 days'
               OR cf_labeled_at > now() - interval '24 hours'
        """))).mappings().one()
    if r["lab"] >= 200 and r["good"] / r["lab"] < 0.5:
        _f(findings, f"labels:mostly-null:{now:%Y-%m-%d}", "labels", "critical",
           f"only {r['good']}/{r['lab']} decisions labelled in 24h got a usable label — "
           f"the learning loops are training on nothing")
    if r["stuck"] > 500:
        _f(findings, f"labels:backlog:{now:%Y-%m-%d}", "labels", "warn",
           f"{r['stuck']} decisions older than 30h still unlabelled — is the labelling loop running?")


MISSED_RUNUP = 0.015   # a no-trade session whose price later rose >= 1.5% from some entry bar


LIVE_RALLY_PCT = 1.5


async def live_rallies() -> list[dict]:
    """Paper stocks rallying RIGHT NOW with no position held: up >= 1.5% from
    the open or from the day's low. Each comes with the gate's latest reason
    for holding off, so a rally is reviewed while it happens — not discovered
    afterwards. Read-only: it never enters a trade (catching every rally also
    buys every false start; the blocked-entry fair test decides the gate)."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.session_store import list_running_sessions, get_session
    out = []
    for slim in await list_running_sessions():
        if slim.get("mode") != "paper":
            continue
        s = await get_session(slim["id"]) or {}
        if ((s.get("position") or {}).get("status") or "NONE") == "LONG":
            continue
        c = [x for x in (s.get("candles") or []) if float(x.get("close") or 0) > 0]
        if len(c) < 5:
            continue
        first, last = float(c[0]["open"] or c[0]["close"]), float(c[-1]["close"])
        low = min(float(x.get("low") or x["close"]) for x in c)
        from_open = (last / first - 1) * 100 if first else 0
        from_low = (last / low - 1) * 100 if low else 0
        if max(from_open, from_low) < LIVE_RALLY_PCT:
            continue
        async with engine.begin() as conn:
            r = (await conn.execute(text(
                "SELECT candle_time, action, reason FROM session_decisions "
                "WHERE session_id = :sid ORDER BY created_at DESC LIMIT 1"),
                {"sid": slim["id"]})).first()
        reason = ((r[2] or "") if r else "").split("— ", 1)[-1][:160]
        out.append({"symbol": slim.get("symbol"), "session_id": slim["id"],
                    "price": last, "from_open_pct": round(from_open, 2),
                    "from_low_pct": round(from_low, 2),
                    "last_decision": r[1] if r else None, "bar": r[0] if r else None,
                    "gate_reason": reason})
    return sorted(out, key=lambda x: -max(x["from_open_pct"], x["from_low_pct"]))


async def missed_uptrends(hours: int = 3) -> dict:
    """No-trade sessions whose chart went up: which gate stood in the way at the
    start of the rise, and would that entry have paid under the live exits?

    A rise visible in hindsight is not by itself a missed trade: on 2026-11-24,
    over 68 days, the blocked entries of every gate lost money on average, and
    on up-days the live exits captured +0.10% of a +1.8% move. So each miss is
    judged by its counterfactual label (live exits, net of costs), not the chart.
    """
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT m.session_id, m.symbol, m.mode, m.date, d.candle_time, d.price, d.reason, d.cf_pnl_pct
            FROM session_metadata m JOIN session_decisions d ON d.session_id = m.session_id
            WHERE m.completed_at > now() - make_interval(hours => :h)
              AND COALESCE(m.trade_count, 0) = 0 AND d.price > 0
            ORDER BY m.session_id, d.candle_time
        """), {"h": hours})).fetchall()
    sess: dict[str, list] = {}
    for r in rows:
        sess.setdefault(r[0], []).append(r)
    misses, blockers = [], {}
    for sid, rs in sess.items():
        prices = [float(r[5]) for r in rs]
        best, best_i = 0.0, None
        run_max = 0.0
        for i in range(len(rs) - 1, -1, -1):           # max future price, right to left
            if "09:30" <= rs[i][4] < "13:00" and run_max:
                up = run_max / prices[i] - 1
                if up > best:
                    best, best_i = up, i
            run_max = max(run_max, prices[i])
        if best_i is None or best < MISSED_RUNUP:
            continue
        r = rs[best_i]
        body = (r[6] or "").split("— ", 1)[-1]
        gate = body.split("; ")[0].split("(")[0].strip()[:70] or "?"
        blockers[gate] = blockers.get(gate, 0) + 1
        misses.append({"session_id": sid, "symbol": r[1], "mode": r[2], "date": r[3],
                       "from": r[4], "runup_pct": round(best * 100, 2),
                       "first_blocker": gate, "cf_pnl_pct": r[7]})
    # The fair test: EVERY blocked tradeable entry in EVERY no-trade session, up
    # or down. The best bar of an up-session is hindsight and always pays.
    all_cf = [float(r[7]) for rs in sess.values() for r in rs
              if r[7] is not None and "09:30" <= r[4] < "13:00"]
    labelled = [m for m in misses if m["cf_pnl_pct"] is not None]
    paying = [m for m in labelled if float(m["cf_pnl_pct"]) > 0]
    return {"hours": hours, "no_trade_sessions": len(sess), "missed": len(misses),
            "labelled": len(labelled), "would_have_paid": len(paying),
            "avg_cf_pct": round(sum(float(m["cf_pnl_pct"]) for m in labelled) / len(labelled), 3)
                          if labelled else None,
            "blocked_entries_labelled": len(all_cf),
            "blocked_entries_avg_cf_pct": round(sum(all_cf) / len(all_cf), 3) if all_cf else None,
            "top_blockers": sorted(blockers.items(), key=lambda kv: -kv[1])[:5],
            "sessions": sorted(misses, key=lambda m: -m["runup_pct"])[:20]}


_LOSS_REVIEW_KEY = "supervisor:loss_review:{}"     # per-trade post-mortem cache
_LOSS_REVIEW_MAX = 60                              # trades post-mortemed per call


def _vwap_side(ind: dict | None, price: float | None) -> str:
    try:
        v = float((ind or {}).get("vwap") or 0)
        return "above_vwap" if price and v and price > v else "below_vwap" if v else "vwap_unknown"
    except (TypeError, ValueError):
        return "vwap_unknown"


async def loss_review(hours: int = 3) -> dict:
    """Deep-dive EVERY losing trade closed in the window (all modes), via the
    existing session_postmortem.trade_postmortem: price path, entry-vs-exit
    blame, setup, every agent's vote. Then aggregate the causes AGAINST A
    WINNERS CONTROL — a trait common among losers means nothing if winners
    share it (loss lessons were once ranked on losers alone and ranked noise).

    Agent blame is NOT computed from these trades: executed trades are gate-
    selected (a split from them once read gbm +0.31 vs sentiment -0.40 and
    collapsed to t=-0.44). Agent verdicts come from the corpus-wide,
    day-clustered culpability baseline the post-mortem already carries.
    """
    import json as _json
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.redis_client import get_redis
    from app.services.session_postmortem import trade_postmortem
    r = get_redis()
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT trade_id, outcome, pnl_pct, trade_source
            FROM trade_records
            WHERE created_at > now() - make_interval(hours => :h)
              AND trade_source IN ('PAPER','REPLAY','BACKTEST') AND pnl_pct IS NOT NULL
              -- the executor's mirror leg of a session entry (own size, own stop,
              -- no session_id) would count one decision twice
              AND COALESCE(market_context->>'source', '') <> 'trade-executor'
            ORDER BY created_at DESC
        """), {"h": hours})).fetchall()
    losers = [x for x in rows if float(x[2]) < 0]
    winners = [x for x in rows if float(x[2]) >= 0]

    async def report(tid):
        key = _LOSS_REVIEW_KEY.format(tid)
        cached = await r.get(key)
        if cached:
            return _json.loads(cached)
        rep = await trade_postmortem(tid)
        if "error" not in rep:
            slim = {k: rep.get(k) for k in ("trade_id", "symbol", "source", "session_id", "pnl_pct",
                                            "held_minutes", "exit_reason", "loss_reason", "setup",
                                            "causes", "blame", "voted_buy", "entry_price")}
            slim["path"] = {k: (rep.get("price_path") or {}).get(k)
                            for k in ("best_pct", "worst_pct", "ever_green", "best_after_exit_pct")}
            slim["vwap_side"] = _vwap_side(rep.get("indicators"), rep.get("entry_price"))
            slim["agents_flagged"] = [a["agent"] for a in rep.get("agents") or []
                                      if (a.get("action") or "").upper() == "BUY"
                                      # only an established (Bonferroni) culprit: "leans
                                      # culprit (not significant)" flagged volatility at
                                      # t 1.93 / +0.002pp — the baseline's own "no".
                                      and a.get("baseline_verdict") == "culprit"]
            await r.set(key, _json.dumps(slim, default=str), ex=7 * 86400)
            return slim
        return None

    reviewed_l = [x for x in [await report(t[0]) for t in losers[:_LOSS_REVIEW_MAX]] if x]
    reviewed_w = [x for x in [await report(t[0]) for t in winners[:_LOSS_REVIEW_MAX]] if x]

    def tags(rep, loss: bool):
        out = {f"exit:{rep.get('exit_reason')}", f"setup:{rep.get('setup')}", rep.get("vwap_side")}
        if loss:
            out.add(f"blame:{(rep.get('blame') or {}).get('target')}")
        p = rep.get("path") or {}
        if p.get("ever_green") is False:
            out.add("never_green")
        return out

    nl, nw = len(reviewed_l), len(reviewed_w)
    lc: dict[str, int] = {}
    wc: dict[str, int] = {}
    for rep in reviewed_l:
        for t in tags(rep, True):
            lc[t] = lc.get(t, 0) + 1
    for rep in reviewed_w:
        for t in tags(rep, False):
            wc[t] = wc.get(t, 0) + 1
    causes = []
    for t, n in lc.items():
        l_rate = n / nl if nl else 0
        w_rate = wc.get(t, 0) / nw if nw else None
        causes.append({"tag": t, "losers": n, "loser_rate": round(l_rate, 2),
                       "winner_rate": round(w_rate, 2) if w_rate is not None else None,
                       "ratio": round(l_rate / w_rate, 2) if w_rate else None})
    causes.sort(key=lambda c: -c["losers"])
    flagged_agents: dict[str, int] = {}
    for rep in reviewed_l:
        for a in rep.get("agents_flagged") or []:
            flagged_agents[a] = flagged_agents.get(a, 0) + 1
    return {"hours": hours, "trades": len(rows), "losers": len(losers), "winners": len(winners),
            "reviewed_losers": nl, "reviewed_winners": nw,
            "blame": {k: v for k, v in lc.items() if k.startswith("blame:")},
            "causes": causes[:12],
            "agents_flagged_corpus_wide": flagged_agents,
            "losses": sorted(reviewed_l, key=lambda x: float(x.get("pnl_pct") or 0))[:25]}


_TRAIT_BASELINE_KEY = "supervisor:loss_trait_baseline"


async def trait_baseline(force: bool = False) -> dict:
    """Corpus-wide check for loss traits: each setup / VWAP side's cf vs the
    same day's other entries, over ALL labelled decisions, day-clustered.
    Cached 12 h (~30 s to compute). On 2026-11-24 the first two loss-trait
    warnings ('below_vwap' 57% of losers vs 30% of winners; 'pullback_in_trend'
    55% vs 30%) were both the BETTER side corpus-wide — a day's losers are too
    few to name a cause on their own."""
    import json as _json
    import statistics as st
    from collections import defaultdict
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.redis_client import get_redis
    from app.agents.counterfactual import _TRADEABLE_ENTRY_SQL
    from app.services.session_postmortem import classify_setup
    r = get_redis()
    if not force:
        cached = await r.get(_TRAIT_BASELINE_KEY)
        if cached:
            return _json.loads(cached)
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT COALESCE(sm.date,(d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) AS mday,
                   d.cf_pnl_pct, d.indicators, d.price
            FROM session_decisions d LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_pnl_pct IS NOT NULL AND d.indicators ? 'vwap' AND d.price > 0
              AND d.created_at > now() - interval '120 days' """ + _TRADEABLE_ENTRY_SQL))).fetchall()
    by: dict = defaultdict(lambda: defaultdict(list))
    alld: dict = defaultdict(list)
    for d, cf, ind, p in rows:
        v = float(cf)
        alld[d].append(v)
        by[f"setup:{classify_setup(ind, float(p))}"][d].append(v)
        by[_vwap_side(ind, float(p))][d].append(v)
    out = {}
    for tag, dd in by.items():
        diffs = [st.mean(dd[x]) - st.mean(alld[x]) for x in dd if len(dd[x]) >= 10]
        if len(diffs) >= 5 and st.stdev(diffs):
            out[tag] = {"lift_pp": round(st.mean(diffs), 4),
                        "t": round(st.mean(diffs) / (st.stdev(diffs) / len(diffs) ** 0.5), 2),
                        "days": len(diffs)}
    res = {"rows": len(rows), "traits": out}
    await r.set(_TRAIT_BASELINE_KEY, _json.dumps(res), ex=12 * 3600)
    return res


async def _check_losses(findings, now):
    lr = await loss_review(hours=24)
    base = (await trait_baseline())["traits"]
    # A recurring loss trait: common among losers AND clearly rarer among winners.
    for c in lr["causes"]:
        if (c["tag"].startswith(("blame:", "exit:")) or c["losers"] < 10 or c["loser_rate"] < 0.4
                or c["ratio"] is None or c["ratio"] < 1.5 or lr["reviewed_winners"] < 10):
            continue
        b = base.get(c["tag"])
        if not b or b["t"] > -2.0:
            continue        # not worse corpus-wide: a small-sample coincidence
        _f(findings, f"losses:trait:{c['tag']}:{now:%Y-%m-%d}", "losses", "warn",
           f"loss trait '{c['tag']}' on {c['losers']}/{lr['reviewed_losers']} losers in 24h "
           f"({c['loser_rate']:.0%}) vs {c['winner_rate']:.0%} of winners, and corpus-wide "
           f"{b['lift_pp']:+.3f} pp vs same-day entries (t {b['t']}, {b['days']} days) — act on it")
    if lr["agents_flagged_corpus_wide"]:
        _f(findings, f"losses:agent-culprit:{now:%Y-%m-%d}", "losses", "warn",
           f"losing entries had BUY votes from agents the corpus-wide baseline marks culpable: "
           f"{lr['agents_flagged_corpus_wide']}")


async def _check_missed(findings, now):
    # 24 h of decisions takes ~11 s to scan (2026-11-24) — too slow for a
    # 2-minute round on a single-worker backend. The fair-test average moves
    # slowly, so a 10-minute cache costs nothing in detection.
    import json as _json
    from app.utils.redis_client import get_redis
    r = get_redis()
    cached = await r.get("supervisor:missed_24h")
    if cached:
        m = _json.loads(cached)
    else:
        m = await missed_uptrends(hours=24)
        await r.set("supervisor:missed_24h", _json.dumps(m, default=str), ex=600)
    avg = m["blocked_entries_avg_cf_pct"]
    if m["blocked_entries_labelled"] >= 300 and avg is not None and avg > 0:
        _f(findings, f"missed:paying:{now:%Y-%m-%d}", "missed", "warn",
           f"the gate's blocked entries in {m['no_trade_sessions']} no-trade sessions would have "
           f"averaged {avg:+.3f}% net under live exits ({m['blocked_entries_labelled']} entries) — "
           f"it is refusing paying trades; {m['missed']} sessions rose >= 1.5%, "
           f"top first blockers {m['top_blockers'][:3]}")


# The end-to-end checklist, in pipeline order. Each item is the worst finding
# raised against its components this run, or ok.
CHECKLIST = [
    ("infra",          "containers, databases, queues up",          ("infra", "audit")),
    ("calendar",       "trading day known; nothing trades a holiday", ("calendar",)),
    ("autopilot",      "autopilot armed, flag not about to expire",  ("autopilot",)),
    ("market_data",    "bars are today's, fresh, quotes sane",       ("market_data",)),
    ("scanner",        "watchlist fresh, full universe",             ("scanner",)),
    ("runner",         "every running session is deciding",          ("runner",)),
    ("agents",         "all agents voting, none erroring/frozen",    ("agents",)),
    ("gate",           "ensemble/gate not in a veto storm",          ("ensemble",)),
    ("records",        "trade records well-formed",                  ("records",)),
    ("training_data",  "trades fed to learning are physically possible", ("training_data",)),
    ("labels",         "decisions get counterfactual labels",        ("labels",)),
    ("learning_loops", "nightly learners ran without error",         ("learning_loops",)),
    ("missed_uptrends", "no-trade sessions that rose did not skip paying entries", ("missed",)),
    ("loss_review",    "every loss post-mortemed; no trait recurs vs winners", ("losses",)),
]


def _checklist(findings: list[dict], ran: set[str]) -> list[dict]:
    order = {"critical": 0, "warn": 1}
    out = []
    for item, what, comps in CHECKLIST:
        hits = [f for f in findings if f["component"] in comps and f["severity"] in order]
        if hits:
            worst = min(hits, key=lambda f: order[f["severity"]])
            out.append({"item": item, "what": what, "status": worst["severity"],
                        "detail": worst["detail"], "n": len(hits)})
        else:
            out.append({"item": item, "what": what,
                        "status": "ok" if item in ran or item in ("calendar", "gate") else "not_run"})
    return out


async def run_audit(full: bool = False) -> dict:
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
    steps = [
        ("market_data", lambda: _check_market_data(findings, now, paper, open_now)),
        ("groww", lambda: _check_groww_stream(findings, r, open_now)),
        ("scanner", lambda: _check_scanner(findings, r, now, trading_day, open_now)),
        ("agents", lambda: _check_decisions(findings, now, paper, open_now)),
        ("records", lambda: _check_records(findings, now)),
        ("training_data", lambda: _check_training_data(findings, now)),
        ("autopilot", lambda: _check_autopilot(findings, r, now, trading_day)),
        ("runner", lambda: _check_runners(findings, now)),
    ]
    if full:   # the slow ones (~4 s): only on a supervisor round, not every poll
        steps += [("labels", lambda: _check_labels(findings, now)),
                  ("missed_uptrends", lambda: _check_missed(findings, now)),
                  ("loss_review", lambda: _check_losses(findings, now)),
                  ("infra", lambda: _check_infra(findings, now))]
    for name, fn in steps:
        try:
            await fn()
            checks.append(name)
        except Exception as exc:
            logger.warning("component audit %s failed", name, exc_info=True)
            _f(findings, f"audit:{name}-error:{now:%Y-%m-%d-%H}", "audit", "warn",
               f"{name} check failed: {exc}")
    ran = set(checks) | ({"learning_loops"} if "infra" in checks else set())
    order = {"critical": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda x: order.get(x["severity"], 3))
    return {"generated_at": now.isoformat(timespec="seconds"), "trading_day": trading_day,
            "market_open": open_now, "paper_sessions": len(paper),
            "open_positions": sum(1 for s in paper if (s.get("position") or {}).get("status") == "LONG"),
            "checks": checks, "checklist": _checklist(findings, ran), "findings": findings}
