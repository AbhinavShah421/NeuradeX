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
                              OR COALESCE(exit_price,0) <= 0)         AS bad_price,
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


async def _check_missed(findings, now):
    m = await missed_uptrends(hours=24)
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
