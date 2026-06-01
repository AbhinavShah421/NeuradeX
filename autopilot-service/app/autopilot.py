"""Autopilot — independent service that trains the agents by auto-trading the AI
watchlist, in two modes:

  • paper    — during market hours, opens a live paper session for every
               watchlist stock (one per symbol per day). Real-time training.

  • backtest — runs continuously (any time). It picks the AI watchlist and opens
               1x replay sessions for the **last trading day**; when that whole
               queue of sessions finishes it steps back to the **previous
               trading day** and runs again — walking backwards through history
               so the agents keep training on dense, real intraday data.

Both are just normal server-side sessions (started via the backend's sessions
API), so they show up on the Paper Trading / Live Sessions pages and every
closed trade trains the ensemble (weights + RL + memory). The service owns the
loops; the backend only reads/writes the enable flags and serves status.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx
import redis.asyncio as redis

logger = logging.getLogger("autopilot")
IST = timezone(timedelta(hours=5, minutes=30))

BACKEND_URL   = os.getenv("BACKEND_URL", "http://backend:8000")
WATCHLIST_KEY = "ai_engine:watchlist"

# Redis flags / state (shared with the backend so the UI toggle takes effect)
PAPER_FLAG     = "ai_engine:autopilot_enabled"
BACKTEST_FLAG  = "ai_engine:autopilot_backtest_enabled"
BACKTEST_STATE = "ai_engine:autopilot_backtest_state"

def _paper_started_key(date_str: str) -> str:
    return f"ai_engine:autopilot:started:{date_str}"

# Config
PAPER_MAX   = int(os.getenv("AUTOPILOT_MAX_SESSIONS", "15"))
CAPITAL     = float(os.getenv("AUTOPILOT_CAPITAL", "50000"))
PAPER_TICK  = int(os.getenv("AUTOPILOT_TICK_SECS", "60"))
BT_SPEED    = int(os.getenv("AUTOPILOT_BACKTEST_SPEED", "1"))      # 1x — dense, real-like
BT_MAX      = int(os.getenv("AUTOPILOT_BACKTEST_MAX", "15"))       # sessions per day queue
BT_POLL     = int(os.getenv("AUTOPILOT_BACKTEST_POLL", "15"))      # seconds between queue checks
BT_DAYS_BACK = int(os.getenv("AUTOPILOT_BACKTEST_MAX_DAYS_BACK", "30"))  # how far back, then wrap

MARKET_OPEN_MIN  = 9 * 60 + 15
MARKET_CLOSE_MIN = 15 * 60 + 30

_redis: redis.Redis | None = None


async def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0"
        _redis = await redis.from_url(url, encoding="utf8", decode_responses=True)
    return _redis


# ── Time helpers ──────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _today() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _market_open() -> bool:
    n = _now_ist()
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return MARKET_OPEN_MIN <= m <= MARKET_CLOSE_MIN


def _prev_trading_day(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ── Flags ─────────────────────────────────────────────────────────────────────

async def _flag(key: str) -> bool:
    try:
        r = await _get_redis()
        return (await r.get(key)) == "1"
    except Exception:
        return False


async def set_mode(mode: str, enabled: bool) -> None:
    key = BACKTEST_FLAG if mode == "backtest" else PAPER_FLAG
    try:
        r = await _get_redis()
        await r.set(key, "1" if enabled else "0", ex=86400 * 30)
    except Exception as exc:
        logger.warning("set_mode failed: %s", exc)


# ── Backend HTTP ──────────────────────────────────────────────────────────────

async def _watchlist_symbols() -> list[str]:
    try:
        r = await _get_redis()
        raw = await r.get(WATCHLIST_KEY)
        if raw:
            return [it["symbol"] for it in json.loads(raw).get("items", []) if it.get("symbol")]
    except Exception:
        pass
    return []


async def _start_session(payload: dict) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{BACKEND_URL}/api/sessions/start", json=payload)
            if r.status_code == 200:
                return (r.json().get("data") or {}).get("id")
            logger.debug("start %s -> %s %s", payload.get("symbol"), r.status_code, r.text[:120])
    except Exception as exc:
        logger.debug("start_session error: %s", exc)
    return None


async def _list_sessions() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/sessions")
            if r.status_code == 200:
                return r.json().get("data", []) or []
    except Exception as exc:
        logger.debug("list_sessions error: %s", exc)
    return []


# ── Paper autopilot ───────────────────────────────────────────────────────────

async def _do_paper_tick() -> None:
    if not _market_open():
        return
    syms = await _watchlist_symbols()
    if not syms:
        return
    sessions = await _list_sessions()
    running = {s["symbol"] for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"}
    today = _today()
    r = await _get_redis()
    raw = await r.get(_paper_started_key(today))
    started = set(json.loads(raw)) if raw else set()
    slots = PAPER_MAX - len([1 for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"])

    for sym in syms:
        if slots <= 0:
            break
        if sym in running or sym in started:
            continue
        sid = await _start_session({"mode": "paper", "symbol": sym, "capital": CAPITAL, "speed": 1})
        if sid:
            started.add(sym)
            slots -= 1
            await r.set(_paper_started_key(today), json.dumps(sorted(started)), ex=86400 * 2)
            logger.info("paper autopilot opened %s", sym)
        await asyncio.sleep(0.4)


# ── Backtest autopilot (backward-walking queues) ──────────────────────────────

async def _load_bt_state() -> dict:
    try:
        r = await _get_redis()
        raw = await r.get(BACKTEST_STATE)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


async def _save_bt_state(st: dict) -> None:
    try:
        r = await _get_redis()
        await r.set(BACKTEST_STATE, json.dumps(st), ex=86400 * 30)
    except Exception as exc:
        logger.debug("bt state save failed: %s", exc)


async def _do_backtest_step() -> None:
    st = await _load_bt_state()
    cursor = st.get("cursor") or _prev_trading_day(_today())
    st.setdefault("cursor", cursor)
    st.setdefault("completed_days", 0)
    st.setdefault("days_back", 0)
    queue = st.get("queue") or []

    # No active queue → start one for the cursor day.
    if not queue:
        syms = await _watchlist_symbols()
        if not syms:
            return                         # wait for the scanner's watchlist
        ids: list[str] = []
        for sym in syms[:BT_MAX]:
            sid = await _start_session({
                "mode": "replay", "symbol": sym, "date": cursor,
                "start_time": "09:15", "capital": CAPITAL, "speed": BT_SPEED,
            })
            if sid:
                ids.append(sid)
            await asyncio.sleep(0.4)

        if not ids:
            # Holiday / no intraday data for that day → skip straight to the day before.
            st["cursor"] = _prev_trading_day(cursor)
            st["days_back"] = st.get("days_back", 0) + 1
            if st["days_back"] >= BT_DAYS_BACK:
                st["cursor"] = _prev_trading_day(_today())
                st["days_back"] = 0
            await _save_bt_state(st)
            return

        st.update({"queue": ids, "queue_date": cursor, "queue_total": len(ids),
                   "queue_pending": len(ids), "started_at": _now_ist().isoformat()})
        await _save_bt_state(st)
        logger.info("backtest autopilot started queue for %s (%d sessions)", cursor, len(ids))
        return

    # Active queue → poll for completion.
    sessions = await _list_sessions()
    status_by_id = {s["id"]: s.get("status") for s in sessions}
    pending = [sid for sid in queue if status_by_id.get(sid, "done") == "running"]
    st["queue_pending"] = len(pending)
    if pending:
        await _save_bt_state(st)
        return

    # Queue done → step back to the previous trading day (wrap at the floor).
    done_date = st.get("queue_date", cursor)
    days_back = st.get("days_back", 0) + 1
    next_cursor = _prev_trading_day(done_date)
    if days_back >= BT_DAYS_BACK:
        next_cursor = _prev_trading_day(_today())
        days_back = 0
    st.update({"cursor": next_cursor, "queue": [], "queue_pending": 0, "started_at": None,
               "completed_days": st.get("completed_days", 0) + 1,
               "last_completed": done_date, "days_back": days_back})
    await _save_bt_state(st)
    logger.info("backtest autopilot finished %s → next %s (day %d)", done_date, next_cursor, st["completed_days"])


# ── Loops ─────────────────────────────────────────────────────────────────────

# Serialize ticks so the periodic loop and an on-enable kick can't both start a
# queue at the same time.
_paper_lock = asyncio.Lock()
_bt_lock = asyncio.Lock()


async def _paper_tick() -> None:
    async with _paper_lock:
        await _do_paper_tick()


async def _backtest_step() -> None:
    async with _bt_lock:
        await _do_backtest_step()


async def kick(mode: str) -> None:
    """Run one tick right now (called when a mode is toggled on) so the user sees
    a queue immediately instead of waiting for the next loop cycle."""
    try:
        if mode == "backtest" and await _flag(BACKTEST_FLAG):
            await _backtest_step()
        elif mode == "paper" and await _flag(PAPER_FLAG):
            await _paper_tick()
    except Exception as exc:
        logger.debug("kick %s failed: %s", mode, exc)


async def paper_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            if await _flag(PAPER_FLAG):
                await _paper_tick()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("paper loop error: %s", exc)
        await asyncio.sleep(PAPER_TICK)


async def backtest_loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            if await _flag(BACKTEST_FLAG):
                await _backtest_step()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("backtest loop error: %s", exc)
        await asyncio.sleep(BT_POLL)


# ── Status ────────────────────────────────────────────────────────────────────

async def status() -> dict:
    sessions = await _list_sessions()
    paper_running = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "paper"]
    bt_running    = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "replay"]
    st  = await _load_bt_state()
    syms = await _watchlist_symbols()
    return {
        "paper": {
            "enabled": await _flag(PAPER_FLAG),
            "market_open": _market_open(),
            "running": len(paper_running),
            "max_concurrent": PAPER_MAX,
            "watchlist_size": len(syms),
            "sessions": [{"id": s["id"], "symbol": s["symbol"],
                          "pnl": s.get("pnl", 0.0)} for s in paper_running],
        },
        "backtest": {
            "enabled": await _flag(BACKTEST_FLAG),
            "running": len(bt_running),
            "speed": BT_SPEED,
            "cursor": st.get("cursor"),
            "queue_date": st.get("queue_date"),
            "queue_total": st.get("queue_total", 0),
            "queue_pending": st.get("queue_pending", 0),
            "completed_days": st.get("completed_days", 0),
            "last_completed": st.get("last_completed"),
            "watchlist_size": len(syms),
        },
    }
