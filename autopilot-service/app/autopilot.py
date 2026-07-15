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
import random
import time
from datetime import datetime, timezone, timedelta

import httpx
import redis.asyncio as redis

logger = logging.getLogger("autopilot")
IST = timezone(timedelta(hours=5, minutes=30))

BACKEND_URL   = os.getenv("BACKEND_URL", "http://backend:8000")
WATCHLIST_KEY = "ai_engine:watchlist"
LIVE_PROMOTIONS_KEY = "ai_engine:live_promotions:{}"   # dated list from the scanner's A-grade live watcher

# Redis flags / state (shared with the backend so the UI toggle takes effect)
PAPER_FLAG     = "ai_engine:autopilot_enabled"
PAPER_TIMING   = "ai_engine:autopilot_paper_timing"   # "normal" | "aggressive"
BACKTEST_FLAG  = "ai_engine:autopilot_backtest_enabled"
BACKTEST_STATE = "ai_engine:autopilot_backtest_state"

def _paper_started_key(date_str: str) -> str:
    return f"ai_engine:autopilot:started:{date_str}"

# Config
PAPER_MAX   = int(os.getenv("AUTOPILOT_MAX_SESSIONS", "15"))
CAPITAL     = float(os.getenv("AUTOPILOT_CAPITAL", "50000"))
PAPER_TICK  = int(os.getenv("AUTOPILOT_TICK_SECS", "60"))
# Per-trade hold cap (minutes): force-exit any position the autopilot's paper
# sessions hold longer than this. 0 = disabled (positions exit on the normal
# intraday/ensemble rules + end-of-day square-off).
MAX_HOLD_MIN = int(os.getenv("AUTOPILOT_MAX_HOLD_MINUTES", "0"))
BT_SPEED      = int(os.getenv("AUTOPILOT_BACKTEST_SPEED", "1"))        # 1x — dense, real-like
BT_BATCH_SIZE = int(os.getenv("AUTOPILOT_BACKTEST_BATCH_SIZE", "7"))    # concurrent sessions per batch
BT_POLL       = int(os.getenv("AUTOPILOT_BACKTEST_POLL", "15"))         # seconds between queue checks
BT_DAYS_BACK  = int(os.getenv("AUTOPILOT_BACKTEST_MAX_DAYS_BACK", "90"))  # how far back, then wrap

# Fixed training universe for the backtest autopilot.
# Using today's live watchlist to replay historical dates is look-ahead bias —
# those stocks are "interesting" because they're hot TODAY, not because they were
# selected on the cursor date. A static, date-independent universe eliminates that.
# Override via AUTOPILOT_BACKTEST_UNIVERSE env var (comma-separated symbols).
_BT_UNIVERSE_DEFAULT = (
    "SBIN,HDFCBANK,ICICIBANK,KOTAKBANK,AXISBANK,"
    "RELIANCE,TCS,INFY,WIPRO,BAJFINANCE,"
    "TATAMOTORS,MARUTI,SUNPHARMA,TITAN,ITC,"
    "HINDUNILVR,NESTLEIND,ULTRACEMCO,ADANIENT,"
    "FEDERALBNK,PNB,IDBI,INDUSINDBK,"
    "SUZLON,IREDA,JKTYRE,ZEEL"
)
_BT_UNIVERSE: list[str] = [
    s.strip() for s in
    os.getenv("AUTOPILOT_BACKTEST_UNIVERSE", _BT_UNIVERSE_DEFAULT).split(",")
    if s.strip()
]

MARKET_OPEN_MIN  = 9 * 60 + 15
MARKET_CLOSE_MIN = 15 * 60 + 30
# The backend blocks new paper ENTRIES after ~13:00 IST (its "auto" no-entry
# cutoff), so a session opened shortly before that is a spectator for the rest
# of the day. 2026-07-15: six A-grade watcher promotions landed 12:26-12:47,
# got 13-34 minutes of permitted entry time, took zero trades, and then polled
# uselessly until close. Don't open a session that can't realistically trade.
PAPER_ENTRY_CUTOFF_MIN = int(os.getenv("AUTOPILOT_ENTRY_CUTOFF_MIN", str(13 * 60)))
MIN_ENTRY_WINDOW_MIN   = int(os.getenv("AUTOPILOT_MIN_ENTRY_WINDOW_MIN", "30"))

# Backtest only runs *outside* the paper-trading window so the agents focus
# entirely on live paper trading during market hours. It is allowed before the
# morning cutoff (09:00) and again after the evening resume (15:40, post-close);
# in between it closes its queue and starts nothing.
BT_MORNING_CUTOFF = int(os.getenv("AUTOPILOT_BACKTEST_MORNING_CUTOFF", str(9 * 60)))       # 09:00
BT_EVENING_RESUME = int(os.getenv("AUTOPILOT_BACKTEST_EVENING_RESUME", str(15 * 60 + 40))) # 15:40

_redis: redis.Redis | None = None


async def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0"
        _redis = await redis.from_url(url, encoding="utf8", decode_responses=True)
    return _redis


# ── Dynamic batch size (concurrent backtest sessions per batch) ─────────────────
# Editable at runtime from the dashboard; persisted in Redis so it survives a
# restart. Falls back to the AUTOPILOT_BACKTEST_BATCH_SIZE env default.
_BATCH_SIZE_KEY = "ai_engine:autopilot_bt_batch_size"
_BATCH_SIZE_MIN, _BATCH_SIZE_MAX = 1, 50
_bt_batch_size: int | None = None   # runtime cache, lazily loaded from Redis


async def _get_batch_size() -> int:
    global _bt_batch_size
    if _bt_batch_size is None:
        try:
            raw = await (await _get_redis()).get(_BATCH_SIZE_KEY)
            _bt_batch_size = int(raw) if raw else BT_BATCH_SIZE
        except Exception:
            _bt_batch_size = BT_BATCH_SIZE
    return _bt_batch_size


async def _set_batch_size(n: int) -> int:
    global _bt_batch_size
    n = max(_BATCH_SIZE_MIN, min(_BATCH_SIZE_MAX, int(n)))
    _bt_batch_size = n
    try:
        await (await _get_redis()).set(_BATCH_SIZE_KEY, str(n))
    except Exception as exc:
        logger.warning("batch-size persist failed: %s", exc)
    return n


# ── Dynamic replay speed (candles advanced per step in backtest sessions) ───────
_SPEED_KEY = "ai_engine:autopilot_bt_speed"
_SPEED_MIN, _SPEED_MAX = 1, 120
_bt_speed: int | None = None   # runtime cache, lazily loaded from Redis


async def _get_speed() -> int:
    global _bt_speed
    if _bt_speed is None:
        try:
            raw = await (await _get_redis()).get(_SPEED_KEY)
            _bt_speed = int(raw) if raw else BT_SPEED
        except Exception:
            _bt_speed = BT_SPEED
    return _bt_speed


async def _set_speed(n: int) -> int:
    global _bt_speed
    n = max(_SPEED_MIN, min(_SPEED_MAX, int(n)))
    _bt_speed = n
    try:
        await (await _get_redis()).set(_SPEED_KEY, str(n))
    except Exception as exc:
        logger.warning("speed persist failed: %s", exc)
    return n


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


def _backtest_allowed() -> bool:
    """Backtest may run only when paper trading isn't (and won't shortly be)
    active: before the morning cutoff or after the evening resume on weekdays,
    and freely on weekends."""
    n = _now_ist()
    if n.weekday() >= 5:
        return True
    m = n.hour * 60 + n.minute
    return (m < BT_MORNING_CUTOFF) or (m >= BT_EVENING_RESUME)


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
    r = await _get_redis()
    await r.set(key, "1" if enabled else "0", ex=86400 * 30)


async def _paper_timing() -> str:
    """Entry-timing mode for autopilot paper sessions: 'normal' | 'aggressive'."""
    try:
        r = await _get_redis()
        return "aggressive" if (await r.get(PAPER_TIMING)) == "aggressive" else "normal"
    except Exception:
        return "normal"


async def set_paper_timing(mode: str) -> None:
    try:
        r = await _get_redis()
        await r.set(PAPER_TIMING, "aggressive" if mode == "aggressive" else "normal", ex=86400 * 30)
    except Exception as exc:
        logger.warning("set_paper_timing failed: %s", exc)


# ── Backend HTTP ──────────────────────────────────────────────────────────────

_WATCHLIST_MAX_AGE_SECONDS = 4 * 3600  # treat watchlist older than 4h as stale


def _watchlist_fresh(data: dict) -> bool:
    """Return False if the watchlist payload has no timestamp or is older than 4h."""
    updated_at = data.get("updated_at") or data.get("scanned_at")
    if not updated_at:
        return True  # no timestamp field — assume fresh (legacy payload)
    try:
        from datetime import timezone
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < _WATCHLIST_MAX_AGE_SECONDS
    except Exception:
        return True


async def _watchlist_symbols() -> list[str]:
    try:
        r = await _get_redis()
        raw = await r.get(WATCHLIST_KEY)
        if raw:
            data = json.loads(raw)
            if not _watchlist_fresh(data):
                logger.warning("Watchlist is stale (>4h) — skipping autopilot tick")
                return []
            return [it["symbol"] for it in data.get("items", []) if it.get("symbol")]
    except Exception:
        pass
    return []


async def _committed_symbols() -> list[str]:
    """High-conviction (committed) picks only — the selective tier the system
    actually trades. Sorted most-convicted first so sessions open in conviction
    order. Empty means the scanner committed nothing for now."""
    try:
        r = await _get_redis()
        raw = await r.get(WATCHLIST_KEY)
        if raw:
            data = json.loads(raw)
            if not _watchlist_fresh(data):
                logger.warning("Watchlist is stale (>4h) — skipping committed symbols read")
                return []
            comm = data.get("committed") or [it for it in data.get("items", []) if it.get("committed")]
            comm = sorted(comm, key=lambda it: float(it.get("score") or 0), reverse=True)
            return [it["symbol"] for it in comm if it.get("symbol")]
    except Exception:
        pass
    return []


async def _live_promoted_symbols() -> list[str]:
    """Intraday A-grade names the scanner's live watcher promoted today, in
    promotion order. Independent of watchlist freshness — every entry was
    re-scored against the quality gate at promotion time."""
    try:
        r = await _get_redis()
        raw = await r.get(LIVE_PROMOTIONS_KEY.format(_today()))
        if raw:
            return [p["symbol"] for p in json.loads(raw) if p.get("symbol")]
    except Exception:
        pass
    return []


async def _top_conviction_symbols(n: int = 1) -> list[str]:
    """The n most-convicted watchlist stocks (BUY-rated preferred, by score).
    Fallback tier: guarantees paper trading starts with the most convicted
    stock on every trading day even when the committed tier is empty."""
    try:
        r = await _get_redis()
        raw = await r.get(WATCHLIST_KEY)
        if not raw:
            return []
        data = json.loads(raw)
        if not _watchlist_fresh(data):
            return []
        items = [it for it in data.get("items", []) if it.get("symbol")]
        buys = [it for it in items if it.get("action") == "BUY"] or items
        buys.sort(key=lambda it: float(it.get("score") or 0), reverse=True)
        return [it["symbol"] for it in buys[:n]]
    except Exception:
        return []


async def _start_session(payload: dict) -> str | None:
    try:
        tagged = {**payload, "source": "autopilot"}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{BACKEND_URL}/api/sessions/start", json=tagged)
            if r.status_code == 200:
                return (r.json().get("data") or {}).get("id")
            # WARN, not debug — a silently failing start means "autopilot enabled
            # but no sessions running" with nothing in the logs to explain it.
            logger.warning("start %s -> %s %s", payload.get("symbol"), r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("start_session %s error: %s", payload.get("symbol"), exc)
    return None


async def _session_owned_by_autopilot(sid: str) -> bool:
    """Return True only if the session was originally started by this autopilot."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/sessions/{sid}")
        if r.status_code == 200:
            data = r.json().get("data") or {}
            return data.get("source") == "autopilot"
    except Exception:
        pass
    return False


async def _list_sessions() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/sessions")
            if r.status_code == 200:
                return r.json().get("data", []) or []
    except Exception as exc:
        logger.debug("list_sessions error: %s", exc)
    return []


async def _session_statuses() -> dict[str, str]:
    """Lightweight alternative to _list_sessions() — returns {id: status} only.
    ~500 bytes vs ~150 KB for the full list; used for queue polling and paper-
    running checks so we never serialize full session summaries just to read status.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/sessions/statuses")
            if r.status_code == 200:
                return r.json().get("data", {}) or {}
    except Exception as exc:
        logger.debug("session_statuses error: %s", exc)
    return {}


async def _stop_session(sid: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(f"{BACKEND_URL}/api/sessions/{sid}/stop")
    except Exception as exc:
        logger.debug("stop_session %s error: %s", sid, exc)


async def _paper_running() -> bool:
    """Return True if any paper session is currently running."""
    statuses = await _session_statuses()
    # Statuses endpoint gives {id: status}; we need mode too for paper check.
    # Fall back to full list only if statuses endpoint is unavailable.
    if statuses:
        # We can't tell mode from statuses alone — use running index from Redis directly.
        try:
            r = await _get_redis()
            ids = await r.smembers("live_sessions:running")
            for sid in ids:
                raw = await r.get(f"live_session:{sid}")
                if raw:
                    import json as _json
                    s = _json.loads(raw)
                    if s.get("mode") == "paper" and s.get("status") == "running":
                        return True
            return False
        except Exception:
            pass
    sessions = await _list_sessions()
    return any(s.get("status") == "running" and s.get("mode") == "paper" for s in sessions)


async def _stop_backtest_queue(reason: str = "paper-trading window") -> None:
    """Close any running backtest replay queue (called at the morning cutoff so
    the agents are free to focus on live paper trading)."""
    st = await _load_bt_state()
    queue = st.get("queue") or []
    if not queue:
        return
    for sid in queue:
        if not await _session_owned_by_autopilot(sid):
            logger.warning("Refusing to stop session %s — not owned by autopilot", sid)
            continue
        await _stop_session(sid)
    st.update({"queue": [], "queue_pending": 0, "queue_date": None})
    await _save_bt_state(st)
    logger.info("backtest queue closed (%s): %d sessions stopped", reason, len(queue))


async def reset_cursor() -> dict:
    """Reset the backtest walk so the next trade date is the **last trading day
    before today**. Any in-flight replay queue is stopped first. Training history
    (completed_days / last_completed) is preserved — only the walk position moves."""
    st = await _load_bt_state()
    for sid in (st.get("queue") or []):
        await _stop_session(sid)
    new_cursor = _prev_trading_day(_today())
    st.update({
        "cursor": new_cursor,
        "queue": [], "queue_pending": 0, "queue_total": 0,
        "queue_date": None, "started_at": None, "days_back": 0,
    })
    await _save_bt_state(st)
    logger.info("backtest autopilot cursor reset → %s", new_cursor)
    return st


# ── Paper autopilot ───────────────────────────────────────────────────────────

_DAILY_LOSS_LIMIT_PCT = float(os.getenv("AUTOPILOT_DAILY_LOSS_LIMIT_PCT", "5.0"))


async def _daily_pnl_pct() -> float:
    """Return today's total realised P&L across all closed paper sessions as a percentage of capital."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{BACKEND_URL}/api/sessions")
        if resp.status_code != 200:
            return 0.0
        sessions = resp.json().get("data", [])
        today = _today()
        total_pnl = 0.0
        total_capital = 0.0
        for s in sessions:
            if s.get("mode") != "paper":
                continue
            if (s.get("created_at") or "")[:10] != today:
                continue
            if s.get("status") not in ("done", "stopped"):
                continue
            metrics = s.get("metrics") or {}
            total_pnl += metrics.get("total_pnl", 0.0)
            total_capital += s.get("capital", CAPITAL) or CAPITAL
        return (total_pnl / total_capital * 100) if total_capital > 0 else 0.0
    except Exception:
        return 0.0


async def _do_paper_tick() -> None:
    if not _market_open():
        return

    # No new sessions once too little of the entry window remains — the backend
    # refuses entries after its cutoff anyway, so a late-started session can
    # only burn decisions without ever being allowed to trade.
    _m = _now_ist().hour * 60 + _now_ist().minute
    if _m > PAPER_ENTRY_CUTOFF_MIN - MIN_ENTRY_WINDOW_MIN:
        return

    # Daily loss circuit-breaker: halt if losses exceed limit
    daily_pnl = await _daily_pnl_pct()
    if daily_pnl < -_DAILY_LOSS_LIMIT_PCT:
        logger.warning(
            "Daily loss circuit-breaker triggered (%.2f%% loss, limit %.2f%%) — autopilot halted for today",
            daily_pnl, _DAILY_LOSS_LIMIT_PCT,
        )
        return

    # Paper trading acts on committed high-conviction picks (precision tier),
    # most-convicted first, plus any A-grade names the scanner's live watcher
    # promoted intraday. If neither tier has anything, never sit the day out:
    # fall back to the single most-convicted watchlist stock so every trading
    # day starts with at least one live session for the agents.
    committed = await _committed_symbols()
    promoted = await _live_promoted_symbols()
    syms = committed + [s for s in promoted if s not in committed]
    if not syms:
        syms = await _top_conviction_symbols(1)
        if syms:
            logger.info("no committed or live-promoted picks — falling back to top-conviction stock %s", syms[0])
    if not syms:
        return                              # no watchlist at all — nothing to trade
    # Use Redis directly for paper session checks — avoids deserialising all
    # session summaries just to count running paper sessions.
    r = await _get_redis()
    running_ids = await r.smembers("live_sessions:running")
    running = set()
    paper_running_count = 0
    for sid in running_ids:
        raw_s = await r.get(f"live_session:{sid}")
        if raw_s:
            sd = json.loads(raw_s)
            if sd.get("mode") == "paper" and sd.get("status") == "running":
                running.add(sd.get("symbol", ""))
                paper_running_count += 1
    today = _today()
    raw = await r.get(_paper_started_key(today))
    started = set(json.loads(raw)) if raw else set()
    slots = PAPER_MAX - paper_running_count
    timing = await _paper_timing()

    for sym in syms:
        if slots <= 0:
            break
        if sym in running or sym in started:
            continue
        sid = await _start_session({"mode": "paper", "symbol": sym, "capital": CAPITAL, "speed": 1,
                                    "max_hold_minutes": MAX_HOLD_MIN, "timing_mode": timing})
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


async def _rank_by_historical_sentiment(symbols: list[str], date: str) -> list[str]:
    """Call the backend's bulk historical-sentiment endpoint and sort symbols by
    sentiment score for `date`.  Returns symbols ordered: bullish first (score ↓),
    neutral in the middle, bearish last.  Falls back to the original order on error.

    This lets the autopilot train agents on the stocks that actually had news
    catalysts on the backtest date — meaning the sentiment agent (which reads
    ai_engine:sentiment:{SYM}:{DATE}) will produce non-trivial signals during the
    replay, giving more meaningful training data.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{BACKEND_URL}/api/ai-engine/sentiment/historical/bulk",
                json={"symbols": symbols, "date": date},
            )
            if r.status_code != 200:
                logger.debug("historical sentiment bulk returned %s", r.status_code)
                return symbols
        data: dict[str, dict] = r.json().get("data") or {}
        def _score(sym: str) -> float:
            d = data.get(sym, {})
            raw_score = float(d.get("score", 0) or 0)
            # Treat truly empty results (0 headlines) as neutral 0 so they
            # don't falsely outrank stocks with weak-but-real signals.
            if int(d.get("headlines_count", 0)) == 0:
                return 0.0
            return raw_score
        ranked = sorted(symbols, key=_score, reverse=True)  # highest score first
        logger.info(
            "Historical sentiment ranking for %s: top=%s (score=%.2f), bottom=%s (score=%.2f)",
            date, ranked[0], _score(ranked[0]), ranked[-1], _score(ranked[-1]),
        )
        return ranked
    except Exception as exc:
        logger.warning("historical sentiment ranking failed for %s: %s", date, exc)
        return symbols


async def _start_batch(syms: list[str], cursor: str) -> list[str]:
    """Start up to the configured batch size of sessions for `syms` on `cursor`. Returns started IDs."""
    bs = await _get_batch_size()
    sp = await _get_speed()
    ids: list[str] = []
    for sym in syms[:bs]:
        sid = await _start_session({
            "mode": "backtest", "symbol": sym, "date": cursor,
            "start_time": "09:15", "capital": CAPITAL, "speed": sp,
            "max_hold_minutes": MAX_HOLD_MIN,
        })
        if sid:
            ids.append(sid)
        await asyncio.sleep(0.4)
    return ids


async def _do_backtest_step() -> None:
    # Never compete with paper trading: outside the allowed window (i.e. from the
    # 09:00 morning cutoff until the post-close evening resume), close any running
    # queue and start nothing. Also yield if paper sessions are live, as a guard.
    if not _backtest_allowed():
        await _stop_backtest_queue("paper-trading hours")
        return
    if await _paper_running():
        await _stop_backtest_queue("paper sessions active")
        return

    st = await _load_bt_state()
    bs = await _get_batch_size()
    cursor = st.get("cursor") or _prev_trading_day(_today())
    st.setdefault("cursor", cursor)
    st.setdefault("completed_days", 0)
    st.setdefault("days_back", 0)
    queue            = st.get("queue") or []
    syms_remaining   = st.get("symbols_remaining") or []
    batch_idx        = st.get("batch_idx", 0)

    # ── No active batch AND no pending symbols → fresh day ────────────────────
    if not queue and not syms_remaining:
        # Rank the full universe by historical sentiment so the most
        # catalyst-backed stocks come first within each batch.
        candidate_pool = list(_BT_UNIVERSE)
        random.shuffle(candidate_pool)
        ranked = await _rank_by_historical_sentiment(candidate_pool, cursor)

        ids = await _start_batch(ranked, cursor)
        if not ids:
            # Holiday / no data → skip to the previous trading day.
            st["cursor"] = _prev_trading_day(cursor)
            st["days_back"] = st.get("days_back", 0) + 1
            if st["days_back"] >= BT_DAYS_BACK:
                st["cursor"] = _prev_trading_day(_today())
                st["days_back"] = 0
            await _save_bt_state(st)
            return

        remaining = ranked[bs:]   # symbols not yet batched
        total = len(ranked)
        st.update({
            "queue": ids, "queue_date": cursor,
            "queue_total": total, "queue_pending": len(ids),
            "symbols_remaining": remaining, "batch_idx": 1,
            "started_at": _now_ist().isoformat(),
        })
        await _save_bt_state(st)
        logger.info(
            "backtest %s — batch 1/%d started (%d sessions, %d remaining)",
            cursor, -(-total // bs), len(ids), len(remaining),
        )
        return

    # ── Active batch → poll for completion ────────────────────────────────────
    if queue:
        status_by_id = await _session_statuses()
        pending = [sid for sid in queue if status_by_id.get(sid, "done") == "running"]
        st["queue_pending"] = len(pending)
        if pending:
            await _save_bt_state(st)
            return

        # Batch done — start the next one if symbols remain.
        if syms_remaining:
            ids = await _start_batch(syms_remaining, cursor)
            remaining = syms_remaining[bs:]
            batch_idx += 1
            total = st.get("queue_total", len(_BT_UNIVERSE))
            n_batches = -(-total // bs)
            st.update({
                "queue": ids, "queue_pending": len(ids),
                "symbols_remaining": remaining, "batch_idx": batch_idx,
            })
            await _save_bt_state(st)
            logger.info(
                "backtest %s — batch %d/%d started (%d sessions, %d remaining)",
                cursor, batch_idx, n_batches, len(ids), len(remaining),
            )
            return

        # All batches for this date done → advance cursor.
        st["queue"] = []
        st["queue_pending"] = 0

    # ── All batches complete → step back to previous trading day ──────────────
    done_date = st.get("queue_date", cursor)
    days_back = st.get("days_back", 0) + 1
    next_cursor = _prev_trading_day(done_date)
    if days_back >= BT_DAYS_BACK:
        next_cursor = _prev_trading_day(_today())
        days_back = 0
    total_batches = -(-st.get("queue_total", 1) // bs)
    st.update({
        "cursor": next_cursor, "queue": [], "queue_pending": 0,
        "symbols_remaining": [], "batch_idx": 0, "started_at": None,
        "completed_days": st.get("completed_days", 0) + 1,
        "last_completed": done_date, "days_back": days_back,
    })
    await _save_bt_state(st)
    logger.info(
        "backtest finished %s (%d batches of %d) → next %s (day %d)",
        done_date, total_batches, bs, next_cursor, st["completed_days"],
    )
    await _train_pattern_model()


_last_pattern_train = 0.0


async def _train_pattern_model() -> None:
    """Trigger a pattern-only training run on the backend (debounced ~30 min)."""
    global _last_pattern_train
    if time.time() - _last_pattern_train < 1800:
        return
    _last_pattern_train = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            await c.post(f"{BACKEND_URL}/api/ai-engine/pattern-model/train",
                         json={"lookback_days": 365, "horizon": 3, "stride": 1})
        logger.info("backtest autopilot kicked pattern-model training")
    except Exception as exc:
        logger.debug("pattern-model train kick failed: %s", exc)


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
            if not _market_open():
                logger.info("kick(paper) skipped — market is closed")
                return
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
    bt_running    = [s for s in sessions if s.get("status") == "running" and s.get("mode") == "backtest"]
    st  = await _load_bt_state()
    syms = await _watchlist_symbols()
    committed = await _committed_symbols()
    bs = await _get_batch_size()
    sp = await _get_speed()
    return {
        "paper": {
            "enabled": await _flag(PAPER_FLAG),
            "market_open": _market_open(),
            "timing_mode": await _paper_timing(),
            "running": len(paper_running),
            "max_concurrent": PAPER_MAX,
            "watchlist_size": len(syms),
            "committed_size": len(committed),
            "source": "committed",
            "sessions": [{"id": s["id"], "symbol": s["symbol"],
                          "pnl": s.get("pnl", 0.0)} for s in paper_running],
        },
        "backtest": {
            "enabled": await _flag(BACKTEST_FLAG),
            "active_window": _backtest_allowed(),
            "running": len(bt_running),
            "speed": sp,
            "batch_size": bs,
            "cursor": st.get("cursor") or _prev_trading_day(_today()),
            "queue_date": st.get("queue_date"),
            "queue_total": st.get("queue_total", 0),
            "queue_pending": st.get("queue_pending", 0),
            "batch_idx": st.get("batch_idx", 0),
            "batches_total": -(-st.get("queue_total", 1) // bs) if st.get("queue_total") else 0,
            "symbols_remaining": len(st.get("symbols_remaining") or []),
            "completed_days": st.get("completed_days", 0),
            "last_completed": st.get("last_completed"),
            "universe_size": len(_BT_UNIVERSE),
            "stock_selection": "fixed_universe",
        },
    }
