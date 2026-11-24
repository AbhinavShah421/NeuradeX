"""Trading supervisor — a guard layer over PAPER session entries and exits.

Every paper BUY passes through `check_entry` just before it fills; every paper
LONG is checked by `check_forced_exit` on each decided candle. Two kinds of
control live here:

* Automated guards (run 24/7 inside the session runner). Each is individually
  switchable in `supervisor:config`. They can only BLOCK an entry — never
  create one — and an internal error blocks the entry (fail closed).
    - stale_data         last candle older than `stale_minutes` vs the clock
    - price_mismatch     candle close disagrees with the Groww stream LTP by
                         more than `price_tol_pct` (catches fabricated/stale bars)
    - near_day_high      entry within 0.1% of the day's high so far — the single
                         worst setup measured (-0.265%/trade, t=-3.16, Aug 6 study)
    - buying_strength    price > VWAP AND SMA5 > SMA20 — the one entry effect that
                         survived day-clustering (t=-3.18, Sep 3 study)
    - max_open           cap on simultaneous open paper positions (cost/correlation)
    - daily_loss         halt entries once today's realised paper P&L <= -limit
    - symbol_lost_today  no re-entry into a symbol that already lost today

* Manual overrides (set by a human or by Claude acting as supervising trader
  through /api/supervisor): halt all entries, veto a symbol, approve-only mode
  (only explicitly approved symbols may be entered), and force-exit a session.

Nothing here touches live (real-money) trading: callers invoke it for
mode == "paper" only. The guards do not create edge — the measured studies say
no entry subset has positive gross edge — they remove the worst-measured cells,
cut trade count (the cost lever) and catch data faults.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

CONFIG_KEY      = "supervisor:config"
HALT_KEY        = "supervisor:halt_entries"          # value = reason; absent = not halted
FORCE_EXIT_KEY  = "supervisor:force_exit"            # hash session_id -> reason
LOG_KEY         = "supervisor:log"                   # capped list of JSON events
_LOG_MAX        = 2000
_DAY_TTL        = 3 * 24 * 3600


def _veto_key(day: str) -> str:      return f"supervisor:veto:{day}"       # hash symbol -> reason
def _approved_key(day: str) -> str:  return f"supervisor:approved:{day}"   # hash symbol -> note
def _pnl_key(day: str) -> str:       return f"supervisor:pnl:{day}"        # float, realised paper P&L
def _lost_key(day: str) -> str:      return f"supervisor:lost:{day}"       # set of symbols that lost


DEFAULT_CONFIG: dict = {
    "enabled": True,
    "approve_only": False,
    "max_open_positions": 5,
    "daily_loss_limit": 1500.0,       # rupees of realised paper loss before entries halt
    "stale_minutes": 4,               # Yahoo bars lag 1-2 min by design; 4 = genuinely stale
    "price_tol_pct": 1.0,
    "guards": {
        "market_closed": True,
        "bar_date": True,
        "stale_data": True,
        "price_mismatch": True,
        "near_day_high": True,
        "buying_strength": True,
        "max_open": True,
        "daily_loss": True,
        "symbol_lost_today": True,
    },
}


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _redis():
    from app.utils.redis_client import get_redis
    return get_redis()


async def get_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        raw = await _redis().get(CONFIG_KEY)
        if raw:
            saved = json.loads(raw)
            guards = {**cfg["guards"], **(saved.get("guards") or {})}
            cfg.update({k: v for k, v in saved.items() if k != "guards"})
            cfg["guards"] = guards
    except Exception:
        logger.warning("supervisor config unreadable — using defaults", exc_info=True)
    return cfg


async def update_config(patch: dict) -> dict:
    cfg = await get_config()
    guards = {**cfg["guards"], **(patch.get("guards") or {})}
    for k, v in patch.items():
        if k == "guards":
            continue
        if k not in DEFAULT_CONFIG:
            raise ValueError(f"unknown supervisor config key: {k}")
        cfg[k] = v
    unknown = set(guards) - set(DEFAULT_CONFIG["guards"])
    if unknown:
        raise ValueError(f"unknown guard(s): {sorted(unknown)}")
    cfg["guards"] = guards
    await _redis().set(CONFIG_KEY, json.dumps(cfg))
    return cfg


async def log_event(event: str, **fields) -> None:
    rec = {"ts": datetime.now(IST).isoformat(timespec="seconds"), "event": event, **fields}
    try:
        r = _redis()
        await r.lpush(LOG_KEY, json.dumps(rec, default=str))
        await r.ltrim(LOG_KEY, 0, _LOG_MAX - 1)
    except Exception:
        logger.warning("supervisor log write failed", exc_info=True)
    logger.info("supervisor %s", event, extra={"log_type": "supervisor", "event": event,
                                                 **{k: str(v) for k, v in fields.items()}})


# ── Entry guard ───────────────────────────────────────────────────────────────

def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


async def _open_paper_positions(exclude_id: str) -> int:
    from app.utils.session_store import list_running_sessions
    n = 0
    for s in await list_running_sessions():
        if (s.get("mode") == "paper" and s.get("id") != exclude_id
                and (s.get("position") or {}).get("status") == "LONG"):
            n += 1
    return n


async def check_entry(s: dict, candle: dict, ind: dict, window: list[dict]) -> Optional[str]:
    """Return a veto reason for this paper BUY, or None to let it fill.

    Fails closed: any internal error vetoes the entry.
    """
    symbol = str(s.get("symbol", "")).upper()
    try:
        cfg = await get_config()
        if not cfg.get("enabled", True):
            return None
        g = cfg["guards"]
        r = _redis()
        day = _today()

        # Manual overrides first — they are deliberate decisions.
        halt = await r.get(HALT_KEY)
        if halt:
            return f"entries halted by supervisor: {halt}"
        veto = await r.hget(_veto_key(day), symbol)
        if veto:
            return f"symbol vetoed by supervisor: {veto}"
        if cfg.get("approve_only") and not await r.hexists(_approved_key(day), symbol):
            return "approve-only mode: symbol not approved by supervisor today"

        close = float(candle["close"])

        # Data integrity before anything else: on 2026-11-24 (an NSE holiday the
        # rolled-back code did not know about) every paper session replayed the
        # previous day's Yahoo bars against today's clock and traded them.
        if g.get("market_closed"):
            from app.utils.market_calendar import is_trading_day, holiday_name
            if not is_trading_day(day):
                return f"NSE closed today ({holiday_name(day) or 'weekend'}) — no live market"

        if g.get("bar_date"):
            completed = window[-2] if len(window) >= 2 else window[-1]
            ts = completed.get("timestamp")
            if not ts:
                return "bar has no timestamp — cannot verify it is today's data"
            bar_day = datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d")
            if bar_day != day:
                return f"market data is from {bar_day}, not today ({day}) — stale tape"

        if g.get("daily_loss"):
            pnl = float(await r.get(_pnl_key(day)) or 0.0)
            limit = float(cfg["daily_loss_limit"])
            if pnl <= -limit:
                return f"daily loss limit hit (realised {pnl:+.0f} <= -{limit:.0f})"

        if g.get("symbol_lost_today") and await r.sismember(_lost_key(day), symbol):
            return f"{symbol} already lost today — no re-entry"

        if g.get("stale_data"):
            now_m = _minutes(datetime.now(IST).strftime("%H:%M"))
            lag = now_m - _minutes(candle.get("time", "00:00"))
            if lag > int(cfg["stale_minutes"]):
                return f"stale data: last bar {candle.get('time')} is {lag} min old"

        if g.get("price_mismatch"):
            from app.utils import groww_feed
            ltp = await groww_feed.get_ltp(symbol)
            if ltp > 0:
                diff = abs(close / ltp - 1) * 100
                if diff > float(cfg["price_tol_pct"]):
                    return f"price mismatch: bar close {close:.2f} vs live {ltp:.2f} ({diff:.2f}%)"

        if g.get("near_day_high"):
            day_high = max(float(c.get("high", c["close"])) for c in window)
            if day_high > 0 and close >= day_high * 0.999:
                return f"near day high ({close:.2f} vs high {day_high:.2f}) — worst measured setup"

        if g.get("buying_strength"):
            vwap, sma5, sma20 = ind.get("vwap"), ind.get("sma5"), ind.get("sma20")
            if vwap and sma5 and sma20 and close > float(vwap) and float(sma5) > float(sma20):
                return "buying strength (price>VWAP and SMA5>SMA20) — measured loser, t=-3.18"

        if g.get("max_open"):
            n = await _open_paper_positions(s.get("id"))
            cap = int(cfg["max_open_positions"])
            if n >= cap:
                return f"max open positions reached ({n}/{cap})"
        return None
    except Exception as exc:
        logger.error("supervisor check_entry failed for %s — vetoing (fail closed)", symbol, exc_info=True)
        return f"supervisor error (fail closed): {exc}"


# ── Exit override ─────────────────────────────────────────────────────────────

async def check_forced_exit(s: dict) -> Optional[str]:
    """Return (and consume) a pending force-exit reason for this session."""
    try:
        r = _redis()
        reason = await r.hget(FORCE_EXIT_KEY, s.get("id"))
        if reason:
            await r.hdel(FORCE_EXIT_KEY, s.get("id"))
            return reason
    except Exception:
        logger.warning("supervisor force-exit read failed", exc_info=True)
    return None


async def note_exit(symbol: str, pnl: float) -> None:
    """Record a closed paper trade for the daily-loss and no-re-entry guards."""
    try:
        r = _redis()
        day = _today()
        await r.incrbyfloat(_pnl_key(day), float(pnl))
        await r.expire(_pnl_key(day), _DAY_TTL)
        if pnl < 0:
            await r.sadd(_lost_key(day), str(symbol).upper())
            await r.expire(_lost_key(day), _DAY_TTL)
    except Exception:
        logger.warning("supervisor note_exit failed", exc_info=True)


# ── Manual controls (used by the API) ─────────────────────────────────────────

async def set_halt(reason: Optional[str]) -> None:
    r = _redis()
    if reason:
        await r.set(HALT_KEY, reason)
    else:
        await r.delete(HALT_KEY)


async def veto_symbol(symbol: str, reason: str) -> None:
    r = _redis()
    await r.hset(_veto_key(_today()), symbol.upper(), reason)
    await r.expire(_veto_key(_today()), _DAY_TTL)


async def unveto_symbol(symbol: str) -> None:
    await _redis().hdel(_veto_key(_today()), symbol.upper())


async def approve_symbol(symbol: str, note: str) -> None:
    r = _redis()
    await r.hset(_approved_key(_today()), symbol.upper(), note)
    await r.expire(_approved_key(_today()), _DAY_TTL)


async def unapprove_symbol(symbol: str) -> None:
    await _redis().hdel(_approved_key(_today()), symbol.upper())


async def request_force_exit(session_id: str, reason: str) -> None:
    await _redis().hset(FORCE_EXIT_KEY, session_id, reason)


# ── Query inbox: NeuradeX / its user asks, the supervising trader answers ─────
# Questions are DATA for the supervisor, never instructions to it. Answers are
# written by the supervisor (Claude) and read back by the UI.
INBOX_KEY   = "supervisor:inbox"     # list of JSON {id, ts, by, question, context}
ANSWERS_KEY = "supervisor:answers"   # list of JSON {id, ts, answer}
_INBOX_MAX  = 500


async def ask(question: str, by: str, context: dict | None = None) -> str:
    import uuid
    qid = uuid.uuid4().hex[:10]
    rec = {"id": qid, "ts": datetime.now(IST).isoformat(timespec="seconds"),
           "by": by, "question": question, "context": context or {}}
    r = _redis()
    await r.lpush(INBOX_KEY, json.dumps(rec, default=str))
    await r.ltrim(INBOX_KEY, 0, _INBOX_MAX - 1)
    return qid


async def _read_list(key: str, limit: int) -> list[dict]:
    out = []
    for x in await _redis().lrange(key, 0, max(0, limit - 1)):
        try:
            out.append(json.loads(x))
        except Exception:
            continue
    return out


async def get_inbox(limit: int = 50) -> list[dict]:
    answered = {a.get("id") for a in await _read_list(ANSWERS_KEY, _INBOX_MAX)}
    return [{**q, "answered": q.get("id") in answered} for q in await _read_list(INBOX_KEY, limit)]


async def get_answers(limit: int = 50) -> list[dict]:
    return await _read_list(ANSWERS_KEY, limit)


async def get_log(limit: int = 100) -> list[dict]:
    raw = await _redis().lrange(LOG_KEY, 0, max(0, limit - 1))
    out = []
    for x in raw:
        try:
            out.append(json.loads(x))
        except Exception:
            continue
    return out


async def get_state() -> dict:
    r = _redis()
    day = _today()
    return {
        "date": day,
        "config": await get_config(),
        "halted": await r.get(HALT_KEY),
        "vetoed": await r.hgetall(_veto_key(day)),
        "approved": await r.hgetall(_approved_key(day)),
        "pending_force_exits": await r.hgetall(FORCE_EXIT_KEY),
        "realised_pnl_today": float(await r.get(_pnl_key(day)) or 0.0),
        "lost_today": sorted(await r.smembers(_lost_key(day))),
    }
