"""Live Trading Sessions business logic — server-side, background-advancing
trade sessions.

A session (AI Live Trading *replay* of a past day, or live *paper* trading) runs
on the server, not in the browser. State lives in Redis, a background loop
advances every running session candle-by-candle using the full 7-agent ensemble
(+ pattern memory), and the frontend simply reads state. This is what makes a
session survive a refresh, keep running in the background, run alongside others,
and be reopenable as a live chart.

This module holds the actual DB/data-provider access, calculations, order
execution and the background advancement loop that used to live inline inside
the FastAPI route handlers (and module-level helpers) in app.api.sessions.
app.api.sessions is now a thin router that parses requests and delegates here.

A few names defined in this module are imported directly by other modules —
app.api.sessions re-exports them for backward compatibility, so do not
rename/remove them without also updating those call sites:
  - app.main: session_runner_loop
  - app.services.ai_engine_service: get_trade_gate, TRADE_GATES,
    TRADE_GATE_KEY, _gate_cache
"""
from __future__ import annotations
import asyncio
import httpx
import os
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.config import settings
from app.utils.elk_logger import get_logger
from app.utils.session_store import (
    save_session, get_session, get_session_slim, delete_session,
    list_sessions, list_sessions_slim, list_running_sessions,
)

# Replay machinery
from app.api.backtest import (
    IST, _SQUAREOFF_MINUTES, _MARKET_OPEN_MINUTES,
    _intraday_indicators, _tech_signal, _time_to_minutes, _compute_metrics,
    _build_trade_record, _derive_agent_signals, _save_backtest_trades,
    _prev_trading_day, _fetch_full_day_candles, _no_real_intraday_msg,
)

logger = get_logger(__name__)

_TICK_SECONDS = 10         # background loop cadence (was 5 — doubled to halve runner CPU)
SPEEDS        = (1, 2, 5, 10)

# ── Trade gate ────────────────────────────────────────────────────────────────
# How selective session entries are; switchable at runtime from the dashboard.
TRADE_GATE_KEY = "ai_engine:trade_gate"
# NOTE on `max_conf` (confidence ceiling): post-trade analysis of 7k+ intraday
# trades showed ensemble confidence is *anti-predictive* above ~0.70 — the most
# "confident" entries (momentum chasing) reverse intraday and lose the most
# (win-rate 16% at >0.90 vs 40% at 0.50–0.60). So disciplined gates also skip
# OVER-confident setups. Expectancy on the [floor, ceiling] band is positive
# where the unbounded gate was negative. "loose" stays uncapped by design.
# Agents whose BUY vote is genuinely predictive. Forensics over the full 14k
# round-trip history (intraday, WIN/LOSS): pattern BUY 48.2% win / +0.38% avg,
# sentiment BUY 44.1% / +0.27% — the only two positive-EV BUY voters. memory
# (21.8%) and gbm (~base-rate) were previously in this set on older, smaller
# analysis and are removed: their BUYs showed no edge at scale.
# rl + meanrev added 2026-07-07: forward-return audit of that day's 2,531 live
# bars showed rl BUY 65% hit / +0.39% avg-30m (n=100) and meanrev 60% / +0.17%
# (n=310), while pattern was near-mute on 1-min bars (since fixed) — the old
# two-agent set left the co-sign gate resting on sentiment alone. Single-day
# evidence at 1-min scale: re-audit after a week of live entries.
_RELIABLE_BUY_AGENTS = frozenset({"sentiment", "pattern", "rl", "meanrev"})

# Trade-gate tuning is data-driven (see analysis):
#   • BUY-vote count predicts win-rate: 1→21%, 2→41%, 3→50% (real ensemble tops
#     out ~2-3 BUYs, so 2 is the practical consensus floor).
#   • Ensemble confidence is ANTI-predictive, monotonically: 39% win at 0.5-0.6,
#     34.6% at 0.6-0.7, then a cliff — 25.3% at 0.7-0.8, 16.3% above 0.9
#     (12.6k trades). Ceilings sit under the 0.70 cliff. The confidence band is
#     only meaningful for a BUY decision, so it's applied ONLY when the
#     ensemble's winning action is BUY (gating it on a HOLD-consensus confidence
#     froze trading entirely).
#   • need_reliable: a BUY must include a proven-positive voter (pattern/
#     sentiment). Now required on "gentle" too: 7.6k sub-30-min churn trades at
#     ~15% win all passed a 2-generic-vote bar that pattern/sentiment mostly
#     didn't co-sign. "loose" stays permissive for training volume.
TRADE_GATES = {
    # override_ceiling: when the ensemble's winning action is NOT BUY but it is
    # *confident* (>= this), don't override it into a long. Post-trade data shows
    # entry-confidence is strongly anti-predictive — win-rate falls 38% (conf 0.6)
    # → 24% (0.8) → 13% (1.0). The worst losers are confident-HOLD overrides.
    "strict": {"label": "Strict", "require_buy": True,  "min_conf": 0.50, "max_conf": 0.68, "override_ceiling": 0.74,
               "min_buy": 3, "need_reliable": True, "min_grade": "B",
               "desc": "Needs 3+ agents (incl. pattern/sentiment) voting BUY, in the 50-68% confidence sweet-spot, with a B+ pattern. Fewest, highest win-rate trades."},
    "gentle": {"label": "Gentle", "require_buy": False, "min_conf": 0.50, "max_conf": 0.68, "override_ceiling": 0.78,
               "min_buy": 2, "need_reliable": True, "min_grade": "C",
               "desc": "Needs 2+ agents voting BUY including pattern or sentiment (the proven BUY voters), a non-bearish ensemble, and a C+ pattern. Balanced."},
    "loose":  {"label": "Loose",  "require_buy": False, "min_conf": 0.0,  "max_conf": 1.01, "override_ceiling": 1.01,
               "min_buy": 2, "need_reliable": False, "min_grade": "D", "reliable_single": True,
               "desc": "Needs 2 BUY votes OR one high-precision agent; pattern grade not enforced. Most trades — for training volume (lower win-rate)."},
}
_gate_cache = {"mode": "", "ts": 0.0}

# ── Per-symbol cold-streak throttle ───────────────────────────────────────────
# Chronic losers keep losing: CGCL won 17% over 123 trades, PNB 20% over 234,
# WIPRO 21% over 114 — while the fleet average is ~28%. A symbol whose recent
# record is that bad gets blocked from NEW entries until its record recovers
# (exits are never blocked). Thresholds are deliberately below base-rate noise:
# with 50 trades the base rate's std-err is ~6pts, so <22% over 30+ is signal.
_SYMBOL_THROTTLE_MIN_TRADES = 30     # need at least this many recent round trips
_SYMBOL_THROTTLE_WIN_BELOW  = 0.22   # block entries while rolling win-rate < this
_SYMBOL_THROTTLE_WINDOW     = 50     # rolling window (most recent trades)
_SYMBOL_THROTTLE_TTL        = 600.0  # per-symbol cache seconds
_symbol_throttle_cache: dict[str, tuple[float, Optional[str]]] = {}   # sym → (ts, reason|None)


async def _symbol_throttle_reason(symbol: str) -> Optional[str]:
    """Block-reason if this symbol is on a cold streak (rolling win-rate below
    _SYMBOL_THROTTLE_WIN_BELOW over its last _SYMBOL_THROTTLE_WINDOW round trips),
    else None. Cached per symbol; fails open on any DB error."""
    import time as _time
    now = _time.monotonic()
    hit = _symbol_throttle_cache.get(symbol)
    if hit and (now - hit[0]) < _SYMBOL_THROTTLE_TTL:
        return hit[1]
    reason: Optional[str] = None
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            row = (await conn.execute(text("""
                SELECT COUNT(*)::int,
                       SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END)::int
                FROM (SELECT outcome FROM trade_records
                      WHERE symbol = :sym AND outcome IN ('WIN','LOSS')
                      ORDER BY created_at DESC
                      LIMIT :win) recent
            """), {"sym": symbol.upper(), "win": _SYMBOL_THROTTLE_WINDOW})).fetchone()
        n, wins = (row[0] or 0), (row[1] or 0)
        if n >= _SYMBOL_THROTTLE_MIN_TRADES and (wins / n) < _SYMBOL_THROTTLE_WIN_BELOW:
            reason = (f"symbol cold-streak: {wins}/{n} recent trades won "
                      f"({wins / n:.0%} < {_SYMBOL_THROTTLE_WIN_BELOW:.0%}) — entries throttled")
    except Exception as exc:
        logger.debug("symbol throttle check skipped for %s: %s", symbol, exc)
    _symbol_throttle_cache[symbol] = (now, reason)
    return reason


def _min_pattern_grade(mode: str) -> str:
    """Required pattern grade to enter, by trading mode.
    Replay uses B (not A) — the memory bank is still building up clean cases so the
    composite score (model P(up) + memory WR) is artificially depressed. B lets
    model-confident setups through while the memory calibrates. Paper/live use B too."""
    if mode in ("replay", "backtest"):
        return os.getenv("PATTERN_MIN_GRADE_BACKTEST", "B").upper()
    return os.getenv("PATTERN_MIN_GRADE_LIVE", "B").upper()


# ── Paper trading time config ─────────────────────────────────────────────────
_PAPER_CONFIG_KEY     = "paper_trading:config"
# Entry cutoff 13:30 (was 14:00): entries in the 14:00 hour win 23.6% and the
# 15:00 hour 21.5%, vs ~30-31% before noon (12.6k-trade forensics). The last
# pre-cutoff hour (13:00) still wins 30.4% — the cliff is at 14:00.
# no_entry_after 13:30 → 13:00 (2026-07-08): 13:00-13:30 entries went 0/8 on
# CF labels (-0.45% avg); the broad population confirms post-13:00 decay (36%).
_PAPER_CONFIG_DEFAULT = {"no_entry_after": "13:00", "squareoff_after": "14:30"}
_paper_cfg_cache: dict = {}
_paper_cfg_ts: float = 0.0


async def get_paper_config() -> dict:
    """Return paper trading time config (cached 30s)."""
    import time as _time
    global _paper_cfg_cache, _paper_cfg_ts
    if _paper_cfg_cache and (_time.monotonic() - _paper_cfg_ts) < 30:
        return _paper_cfg_cache
    cfg = dict(_PAPER_CONFIG_DEFAULT)
    try:
        from app.utils.redis_client import cache_get
        import json as _json
        raw = await cache_get(_PAPER_CONFIG_KEY)
        if raw:
            cfg.update(_json.loads(raw))
    except Exception:
        logger.warning("Failed to read paper-trading config from cache; using defaults", exc_info=True)
    _paper_cfg_cache = cfg
    _paper_cfg_ts = _time.monotonic()
    return cfg


async def save_paper_config(cfg: dict) -> None:
    global _paper_cfg_cache, _paper_cfg_ts
    import json as _json
    try:
        from app.utils.redis_client import get_redis
        await get_redis().set(_PAPER_CONFIG_KEY, _json.dumps(cfg))
    except Exception:
        logger.warning("Failed to persist paper-trading config to cache", exc_info=True)
    _paper_cfg_cache = cfg
    _paper_cfg_ts = 0.0  # invalidate cache so next read is fresh


async def get_trade_gate() -> str:
    """Current gate mode (cached ~15s to avoid a Redis read per candle)."""
    import time
    now = time.monotonic()
    if _gate_cache["mode"] and (now - _gate_cache["ts"]) < 15:
        return _gate_cache["mode"]
    mode = getattr(settings, "TRADE_GATE", "gentle")
    try:
        from app.utils.redis_client import cache_get
        v = await cache_get(TRADE_GATE_KEY)
        if v in TRADE_GATES:
            mode = v
    except Exception:
        logger.warning("Failed to read trade-gate mode from cache; using default %s", mode, exc_info=True)
    if mode not in TRADE_GATES:
        mode = "gentle"
    _gate_cache.update({"mode": mode, "ts": now})
    return mode


_regime_cache = {"regime": "neutral", "ts": 0.0}


async def get_market_regime() -> str:
    """Broad NIFTY regime (bullish/bearish/neutral) the scanner computed, cached
    ~60s. Used to block long entries when the whole market is falling."""
    import time, json
    now = time.monotonic()
    if now - _regime_cache["ts"] < 60:
        return _regime_cache["regime"]
    regime = "neutral"
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:watchlist")
        if raw:
            regime = (json.loads(raw).get("market_regime") or "neutral").lower()
    except Exception:
        logger.debug("Failed to read market regime from cache; defaulting to neutral", exc_info=True)
    _regime_cache.update({"regime": regime, "ts": now})
    return regime


# ── Models ────────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    mode:       str = "replay"           # "replay" | "paper"
    symbol:     str
    date:       Optional[str] = None     # required for replay (YYYY-MM-DD)
    start_time: str = "09:15"
    capital:    float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    speed:      int   = 1
    model:      Optional[str] = None
    # Per-trade hold cap (minutes): force-exit any single position held longer
    # than this. 0 = disabled. Used by auto-traded watchlist stocks.
    max_hold_minutes: int = Field(default=0, ge=0, le=375)
    # Entry-timing aggressiveness: "normal" | "aggressive" (looser triggers → more trades)
    timing_mode: str = "normal"


class SpeedRequest(BaseModel):
    speed: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _idx_for_time(all_candles: list[dict], hhmm: str) -> int:
    target = _time_to_minutes(hhmm)
    idx = 0
    for i, c in enumerate(all_candles):
        if _time_to_minutes(c.get("time", "09:15")) <= target:
            idx = i
        else:
            break
    return idx


def _summary(s: dict) -> dict:
    """List view — strips heavy candle arrays."""
    pos = s.get("position", {})
    return {
        "id":             s["id"],
        "mode":           s["mode"],
        "symbol":         s["symbol"],
        "date":           s.get("date"),
        "status":         s.get("status"),
        "current_time":   s.get("current_time"),
        "capital":        s.get("capital"),
        "cash":           s.get("cash"),
        "pnl":            s.get("metrics", {}).get("total_pnl", 0.0),
        "pnl_pct":        s.get("metrics", {}).get("total_pnl_pct", 0.0),
        "trades":         len(s.get("trades", [])),
        "position":       pos.get("status", "NONE"),
        "speed":          s.get("speed", 1),
        "max_hold_minutes": s.get("max_hold_minutes", 0),
        "timing_mode":    s.get("timing_mode", "normal"),
        "data_source":    s.get("data_source"),
        "agent_action":   s.get("agent_decision", {}).get("action"),
        "agent_confidence": s.get("agent_decision", {}).get("confidence"),
        "last_decision":  s.get("last_decision"),
        "created_at":     s.get("created_at"),
        "updated_at":     s.get("updated_at"),
        "error":          s.get("error"),
    }


def _detail(s: dict) -> dict:
    """Full view for the live chart — candles up to the current point only."""
    if s["mode"] in ("replay", "backtest"):
        idx = s.get("current_idx", 0)
        candles = (s.get("all_candles") or [])[: idx + 1]
    else:
        candles = s.get("candles", [])
    return {
        **_summary(s),
        "candles":          candles,
        "prev_day_candles": s.get("prev_day_candles", []),
        "prev_day_date":    s.get("prev_day_date"),
        "trades_list":      s.get("trades", []),
        "position_detail":  s.get("position", {}),
        "metrics":          s.get("metrics", {}),
        "agent_decision":   s.get("agent_decision", {}),
        "last_decision":    s.get("last_decision"),
        "decision_log":     (s.get("decision_log") or [])[-60:],
        "agents":           s.get("agents", []),
        "model_used":       s.get("model"),
    }


async def _ensemble_decision(symbol: str, candles: list[dict], capital: float, position: str,
                             mode: str = "paper", date: str | None = None):
    """Run the 11-agent ensemble (+memory gate) on the candles seen so far."""
    from app.agents import get_engine
    engine  = get_engine()
    context = {"symbol": symbol, "capital": capital, "position": position, "mode": mode,
               "date": date}  # date is YYYY-MM-DD for replay/backtest; None for paper
    decision = await engine.decide(symbol, candles, context)
    agents = [
        {"agent_name": a.agent_name, "action": a.action,
         "confidence": a.confidence, "weight": round(a.weight, 3),
         "reasoning": a.reasoning, "indicators": a.indicators}
        for a in decision.agents
    ]
    return decision, agents


def _entry_fingerprint(candles: list[dict]):
    try:
        from app.agents.fingerprint import build_fingerprint, classify_regime
        return build_fingerprint(candles), classify_regime(candles)
    except Exception:
        logger.debug("Entry fingerprint build failed; continuing without one", exc_info=True)
        return None, "unknown"


async def _feed_memory(symbol: str, fp, regime: str, pnl_pct: float, entry: float, exit_: float, mode: str):
    if not fp:
        return
    try:
        from app.agents import get_memory
        await get_memory().add_case(
            symbol=symbol, fingerprint=fp, action="BUY", pnl_pct=pnl_pct,
            entry_price=entry, exit_price=exit_, regime=regime,
            source="PAPER" if mode == "paper" else ("BACKTEST" if mode == "backtest" else "REPLAY"),
        )
    except Exception as exc:
        logger.debug("session memory feed skipped: %s", exc)


async def _persist_session_decision(session_id: str, symbol: str, decision: dict) -> None:
    """Write one candle decision to session_decisions (permanent, unlike the Redis
    rolling window). Upserted on (session_id, candle_time): _step re-decides the
    same in-progress candle every ~2s advance tick, and a plain INSERT was writing
    tens of thousands of duplicate rows per day — the final decision for the
    candle is the only one that matters (and the only one the counterfactual
    labeler should learn from)."""
    try:
        import json as _json
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO session_decisions
                    (session_id, symbol, candle_time, price, action, executed,
                     confidence, reason, indicators, agents, trade)
                VALUES (:sid, :sym, :ct, :price, :action, :executed,
                        :conf, :reason, :indicators, :agents, :trade)
                ON CONFLICT (session_id, candle_time) DO UPDATE SET
                    price      = EXCLUDED.price,
                    action     = EXCLUDED.action,
                    executed   = EXCLUDED.executed OR session_decisions.executed,
                    confidence = EXCLUDED.confidence,
                    reason     = EXCLUDED.reason,
                    indicators = EXCLUDED.indicators,
                    agents     = EXCLUDED.agents,
                    trade      = COALESCE(EXCLUDED.trade, session_decisions.trade)
            """), {
                "sid":        session_id,
                "sym":        symbol,
                "ct":         decision.get("time"),
                "price":      decision.get("price"),
                "action":     decision.get("action"),
                "executed":   bool(decision.get("executed", False)),
                "conf":       decision.get("confidence"),
                "reason":     decision.get("reason"),
                "indicators": _json.dumps(decision.get("indicators", {})),
                "agents":     _json.dumps(decision.get("agents", [])),
                "trade":      _json.dumps(decision["trade"]) if decision.get("trade") else None,
            })
    except Exception as exc:
        logger.debug("persist_session_decision failed: %s", exc)


async def _finalize_session(s: dict) -> None:
    """Persist session summary to session_metadata. Called once when session reaches a terminal state."""
    if s.get("_finalized"):
        return
    s["_finalized"] = True
    try:
        import json as _json
        from sqlalchemy import text
        from app.database.postgres import engine
        metrics = s.get("metrics", {})
        sells   = [t for t in (s.get("trades") or []) if t.get("action") == "SELL"]
        wins    = sum(1 for t in sells if t.get("pnl", 0) > 0)
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO session_metadata
                    (session_id, symbol, mode, date, status, capital, final_cash,
                     trade_count, win_count, total_pnl_abs, total_pnl_pct,
                     candle_count, completed_at, session_data)
                VALUES (:sid, :sym, :mode, :date, :status, :capital, :cash,
                        :tc, :wc, :pnl_abs, :pnl_pct, :candles, NOW(), :data)
                ON CONFLICT (session_id) DO UPDATE SET
                    status       = EXCLUDED.status,
                    final_cash   = EXCLUDED.final_cash,
                    trade_count  = EXCLUDED.trade_count,
                    win_count    = EXCLUDED.win_count,
                    total_pnl_abs= EXCLUDED.total_pnl_abs,
                    total_pnl_pct= EXCLUDED.total_pnl_pct,
                    candle_count = EXCLUDED.candle_count,
                    completed_at = EXCLUDED.completed_at,
                    session_data = EXCLUDED.session_data
            """), {
                "sid":     s["id"],
                "sym":     s.get("symbol", ""),
                "mode":    s.get("mode", "paper"),
                "date":    s.get("date", ""),
                "status":  s.get("status", "done"),
                "capital": s.get("capital"),
                "cash":    s.get("cash"),
                "tc":      len(sells),
                "wc":      wins,
                "pnl_abs": metrics.get("total_pnl", 0),
                "pnl_pct": metrics.get("total_return_pct", 0),
                "candles": s.get("current_idx", 0),
                "data":    _json.dumps(metrics),
            })
        logger.info("Session finalized to DB", extra={
            "log_type": "session_event", "event": "session_finalized",
            "session_id": s["id"], "status": s.get("status"), "trades": len(sells),
        })
    except Exception as exc:
        logger.warning("_finalize_session failed: %s", exc)


_PAPER_DROP_CANDLES = 6     # N consecutive lower closes = drop pattern (3 was triggering on normal chop)
_LOSS_COOLDOWN_MIN  = 10    # after a losing exit, block new entries for this many minutes
                            # (stops the system re-scalping the same chop range — the
                            #  TITAN-style 6-losing-round-trips-in-an-afternoon pattern)
_LATE_ENTRY_CUTOFF_MIN = 13 * 60   # 13:00 IST — no new entries after this in
                                        # replay/backtest (paper uses its own
                                        # configurable cutoff). 14:00-hour entries win
                                        # 23.6% and 15:00-hour 21.5% vs ~30-31% before
                                        # noon; 13:00-hour still wins 30.4%.
_MARKET_CLOSE_MINUTES = 15 * 60 + 30   # 15:30 IST NSE close — after this, today's
                                       # session is finished and backtestable.


def _timing_block_reason(ind: dict, candle: dict) -> str:
    """Plain-English explanation of why the intraday timing signal didn't fire an
    entry — so a paper/backtest session can show *why no trade yet*."""
    rsi = ind.get("rsi", 50.0)
    mom = ind.get("mom5", 0.0)
    price = candle.get("close", 0.0)
    vwap = ind.get("vwap", price)
    sma5, sma20 = ind.get("sma5", price), ind.get("sma20", price)
    if sma5 < sma20 and mom < -0.10:
        return (f"downtrend filter active (SMA5 < SMA20, momentum {mom:+.2f}%) — "
                f"holding off to avoid catching a falling knife")
    return (f"no intraday trigger yet — needs an oversold bounce (RSI<38 & price≥VWAP & rising), "
            f"a trend continuation (SMA5≥SMA20 & momentum>0.18%), or a momentum breakout "
            f"(momentum>0.35% & price>VWAP). Now: RSI {rsi:.0f}, momentum {mom:+.2f}%, "
            f"price {'above' if price >= vwap else 'below'} VWAP")


async def _step(s: dict, window: list[dict], force_close: bool) -> None:
    """Run one candle: decide, execute against the session's position, update state."""
    if not window:
        return
    idx    = len(window) - 1
    candle = window[idx]
    ind    = _intraday_indicators(window, idx)
    pos    = s["position"]
    symbol = s["symbol"]
    pos_status  = pos["status"]
    entry_price = pos.get("entry_price", 0.0)

    # Intraday rule signal times the entries/exits (RSI/VWAP/momentum + take-profit,
    # stop-loss, end-of-day square-off). 1 = buy, -1 = sell, 0 = hold.
    aggressive = s.get("timing_mode") == "aggressive"
    # Minutes the current LONG has been held — enables _tech_signal's entry grace
    # period (first minutes: disaster stop only, normal stops suspended).
    held_minutes = None
    if pos_status == "LONG" and pos.get("entry_time") and candle.get("time"):
        try:
            eh, em = map(int, str(pos["entry_time"]).split(":")[:2])
            ch, cm = map(int, str(candle["time"]).split(":")[:2])
            held_minutes = max(0, (ch * 60 + cm) - (eh * 60 + em))
        except (ValueError, AttributeError):
            held_minutes = None
    tsig = -1 if (force_close and pos_status == "LONG") else _tech_signal(
        ind, pos_status, candle, entry_price, aggressive=aggressive, held_minutes=held_minutes)

    # The 11-agent ensemble (+memory gate) is the decision brain: it provides the
    # confidence, the reasoning, and can confirm or veto what the timing signal proposes.
    agents = s.get("agents", [])
    conf   = 0.6
    reason = ""
    if not (force_close and pos_status == "LONG"):
        decision, agents = await _ensemble_decision(symbol, window, s["capital"], pos_status,
                                                      s.get("mode", "paper"), date=s.get("date"))
        conf, reason, ens_action = decision.confidence, decision.reasoning, decision.action
    else:
        ens_action = "SELL"

    # Combine timing signal + ensemble verdict into the final action
    if force_close and pos_status == "LONG":
        action, conf, reason = "SELL", 0.99, "Session end — squared off automatically."
    elif pos_status == "NONE":
        # Entry is governed by the selected trade gate (strict / gentle / loose).
        gate_mode = await get_trade_gate()
        gate = TRADE_GATES[gate_mode]
        blocked: list[str] = []
        # Genuine BUY support: which agents independently voted BUY, and whether
        # any of them is a high-precision agent (sentiment/pattern/memory/gbm).
        buy_voters = {a.get("agent_name") for a in agents if a.get("action") == "BUY"}
        buy_votes  = len(buy_voters)
        has_reliable_buy = bool(buy_voters & _RELIABLE_BUY_AGENTS)
        min_buy = gate.get("min_buy", 2)
        need_reliable = gate.get("need_reliable", False)
        # The entry trigger is the ensemble BUY consensus itself — NOT the narrow
        # oversold-bounce timing signal (the two rarely co-occur, which froze
        # trading). The timing signal is now only a veto: skip entries while it is
        # actively bearish (tsig == -1 / downtrend), but a neutral timing is fine.
        # Entry support: real ensemble consensus is what predicts wins — single-
        # agent BUYs historically lose (~21-25%) while >=2 agreeing win ~41-50%.
        # So a long needs >=min_buy BUY votes. Only the "loose" (training-volume)
        # gate also allows a single high-precision agent's BUY.
        reliable_single = gate.get("reliable_single", False)
        if gate["require_buy"]:
            support_ok = (ens_action == "BUY" and buy_votes >= min_buy)
            if not support_ok:
                blocked.append(f"strict: need ensemble BUY with {min_buy}+ votes (got {ens_action}, {buy_votes} BUY)")
        else:
            support_ok = (buy_votes >= min_buy or (reliable_single and has_reliable_buy and buy_votes >= 1)) and ens_action != "SELL"
            if ens_action == "SELL":
                blocked.append("ensemble is bearish (SELL)")
            elif not support_ok:
                blocked.append(f"insufficient BUY consensus: {buy_votes} agents voted BUY (need {min_buy}+)")
        # Reliable-voter requirement (was declared on the gate but never enforced):
        # a consensus made only of generic voters (technical/rl/momentum/...) wins
        # ~15-29% historically; pattern (48%) or sentiment (44%) must co-sign.
        if need_reliable and support_ok and not has_reliable_buy:
            support_ok = False
            blocked.append("no proven BUY voter (pattern/sentiment) co-signed — "
                           "generic consensus alone historically loses")
        # ── Panel dissent rules (CF-validated 2026-07-07, n=17 entry bars) ────
        # The gate counted BUY voters but was blind to SELL dissent: CMPDI
        # 09:39 entered 2-BUY-vs-2-SELL with meanrev screaming SELL at 0.84
        # and lost -1.66%. On CF-labeled entries, NET consensus (buys - sells)
        # >= 2 won 89% (+0.82%) vs 25% (-0.28%) below it; and a reliable agent
        # dissenting at >=0.75 never occurs on winning entries. Small sample —
        # re-audit after a week of live entries.
        sell_voters_n = sum(1 for a in agents if a.get("action") == "SELL")
        net_consensus = buy_votes - sell_voters_n
        if support_ok and net_consensus < 2:
            support_ok = False
            blocked.append(f"panel dissent: net consensus {buy_votes} BUY - {sell_voters_n} SELL "
                           f"= {net_consensus} (need >= 2) — divided experts historically lose")
        rel_dissent = max((float(a.get("confidence") or 0) for a in agents
                           if a.get("action") == "SELL"
                           and a.get("agent_name") in _RELIABLE_BUY_AGENTS), default=0.0)
        if support_ok and rel_dissent >= 0.75:
            support_ok = False
            blocked.append(f"trusted-expert dissent: a proven agent votes SELL at "
                           f"{rel_dissent:.0%} — not entering against it")
        if tsig == -1:
            blocked.append("intraday signal bearish (downtrend) — skipping entry")
        # ── Cold-streak symbol throttle ───────────────────────────────────────
        # Chronic losers stay losers (CGCL 17% over 123, PNB 20% over 234): a
        # symbol with a rolling win-rate this far below base gets no NEW entries
        # until its record recovers. Cheap: cached per symbol for 10 min.
        throttle = await _symbol_throttle_reason(symbol)
        if throttle:
            blocked.append(throttle)
        # ── Direction filter (Lever 1) ────────────────────────────────────────
        # We are long-only. Post-trade analysis showed 78% of losers were longs
        # opened counter-trend (price below VWAP / SMA5<SMA20) — falling knives.
        # Only enter with the stock's own trend, and never long a bearish market.
        price_now = candle.get("close", 0.0)
        uptrend = (ind.get("sma5", 0) >= ind.get("sma20", 0)) and (price_now >= ind.get("vwap", price_now))
        if not uptrend:
            blocked.append("counter-trend (price below VWAP or SMA5<SMA20) — long only with the trend")
        # NOTE: no hard broad-market veto. The per-stock uptrend filter above is
        # the real direction gate — a stock in an intraday uptrend is a valid long
        # even on a red market day, and a blanket "market is bearish" block just
        # froze all paper trading. (get_market_regime remains available for sizing.)
        # ── Entry-timing filter (Lever 2) ─────────────────────────────────────
        # Within an uptrend, don't buy extreme overbought tops — they reverse fast
        # and are the anti-predictive high-confidence momentum chases. (We do NOT
        # require positive momentum: a pullback within the uptrend is a better long
        # than chasing strength, so a mildly-negative mom is fine.)
        rsi_now = ind.get("rsi", 50.0)
        # RSI entry band [58, 70] — "trade strength, not weakness". Win rate is
        # MONOTONIC in entry RSI across the strict CF-labeled population (n=127):
        #   RSI 45-55  54% win / -0.14%   ← coin flips, negative after costs
        #   RSI 55-60  64%
        #   RSI 60-65  68%
        #   RSI 65-70 100% / +0.42%
        # In a confirmed uptrend, RSI in the 45-55 zone is a stalling/failing
        # bounce, not a pullback to buy. Floor raised 45 → 58 (2026-07-09): on
        # the labeled population this lifts the entry set from 66% → ~83% win
        # and roughly DOUBLES avg P&L, at ~half the trade count — a precision-
        # over-volume choice toward the 90% target. The <45 case keeps its own
        # message since it's the extreme of the same failure.
        _RSI_FLOOR = 58
        good_timing = _RSI_FLOOR <= rsi_now <= 70
        if rsi_now > 70:
            blocked.append(f"overbought (RSI {rsi_now:.0f}>70) — wait for a pullback")
        elif rsi_now < 45:
            blocked.append(f"failing bounce (RSI {rsi_now:.0f}<45) — dip entries in "
                           f"uptrends lost 8/8 on CF labels; wait for strength")
        elif rsi_now < _RSI_FLOOR:
            blocked.append(f"insufficient strength (RSI {rsi_now:.0f}<{_RSI_FLOOR}) — "
                           f"the 45-58 zone wins ~55-60% (coin-flip); wait for RSI>={_RSI_FLOOR}")
        # The confidence band only describes a BUY decision. When the ensemble's
        # winning action is HOLD (gentle/loose entering on BUY support), its
        # confidence is the HOLD confidence — irrelevant to the BUY, so skip it.
        max_conf = gate.get("max_conf", 1.01)
        conf_ok = True
        if ens_action == "BUY":
            # The over-confidence ceiling was calibrated on the LEGACY confidence
            # scale, where a high value meant one lopsided voice. On the
            # directional scale high confidence means unanimity — no dissenting
            # SELL voter anywhere — and CF-labeled data shows those are the best
            # entries (conf>0.90: 8/8 wins +0.94% avg vs 40% in the 0.50-0.80
            # band). Floor still applies in both modes; ceiling is legacy-only.
            directional = getattr(decision, "vote_mode", "legacy") == "directional"
            if conf < gate["min_conf"]:
                blocked.append(f"BUY confidence {conf:.0%} below the {gate['min_conf']:.0%} floor")
                conf_ok = False
            elif conf > max_conf and not directional:
                blocked.append(f"BUY confidence {conf:.0%} above the {max_conf:.0%} ceiling (over-confident setups historically reverse)")
                conf_ok = False
        # ── Confident-override veto (Mistake #1: confidence is anti-predictive) ──
        # Entry confidence is inverted: trades entered at conf ≥0.8 win ~24% / at
        # 1.0 only 13%, vs 38% at 0.6. The worst case is overriding a *confident*
        # non-BUY ensemble with a marginal BUY-vote entry — so block exactly that.
        override_ceiling = gate.get("override_ceiling", 1.01)
        confident_override = (ens_action != "BUY" and conf >= override_ceiling)
        if confident_override:
            blocked.append(
                f"ensemble confidently {ens_action} ({conf:.0%} ≥ {override_ceiling:.0%}) — "
                f"not overriding into a long (high entry-confidence is anti-predictive)"
            )
        enter = (tsig != -1 and support_ok and conf_ok and uptrend and good_timing and not confident_override)
        # ── Post-loss cooldown ────────────────────────────────────────────────
        # After a losing exit, stay out for _LOSS_COOLDOWN_MIN minutes instead of
        # immediately re-entering the same chop range. This is the single biggest
        # cut to the "6 small losing round-trips in one afternoon" pattern.
        if enter:
            cd_until = s.get("cooldown_until_min", 0)
            now_min  = _time_to_minutes(candle.get("time", "09:15"))
            if cd_until and now_min < cd_until:
                enter = False
                blocked.append(
                    f"post-loss cooldown — no re-entry until {cd_until // 60:02d}:{cd_until % 60:02d} "
                    f"({cd_until - now_min}m left)"
                )
        # ── Day-structure veto ────────────────────────────────────────────────
        # The day_structure agent votes SELL when price is in the top tier of the
        # day's range with poor risk/reward for a long (near day high, resistance
        # overhead, extended move). Two independent checks:
        #   (a) a confident SELL vote, and
        #   (b) poor R/R while high in the day range — even if the vote is HOLD,
        #       a long with little upside and the whole day below it is bad expectancy.
        if enter:
            ds_agent = next((a for a in agents if a.get("agent_name") == "day_structure"), None)
            ind_ds   = (ds_agent or {}).get("indicators") or {}
            rng_pct  = ind_ds.get("day_range_pct")
            rr       = ind_ds.get("rr_ratio")
            ds_sell  = ds_agent and ds_agent.get("action") == "SELL" and ds_agent.get("confidence", 0) >= 0.62
            poor_rr_high = (rr is not None and rng_pct is not None and rr < 0.60 and rng_pct > 0.55)
            if ds_sell or poor_rr_high:
                enter = False
                blocked.append(
                    f"day-structure veto: {(rng_pct or 0)*100:.0f}% of day range, "
                    f"R/R {(rr or 0):.1f}× — unfavorable risk/reward for a long entry"
                )
        # ── Tested-ceiling veto ───────────────────────────────────────────────
        # Never open a long INTO a multi-tested resistance from the shared
        # full-day level map: buying <0.15% under a 2+-touch ceiling is the
        # 2026-07-07 AEGISVOPAK 10:11 pattern (entered 0.04% under the level,
        # stopped out in 7 min). A tested level must first break to be a trade.
        if enter:
            lv_res = ind_ds.get("levels_res") or []
            lv_sup = ind_ds.get("levels_sup") or []
            ceiling = next((l for l in lv_res
                            if l.get("touches", 1) >= 2 and l.get("dist_pct", 99) <= 0.15), None)
            # Coil exception: price simultaneously ON a tested support (2+
            # touches within 0.10%) is a compression setup, not a ceiling
            # chase — AVANTEL 11:17 (5x support under a 4x ceiling) broke UP
            # +0.8%. Veto only the naked approach into the ceiling.
            coil = any(l.get("touches", 1) >= 2 and l.get("dist_pct", 99) <= 0.10
                       for l in lv_sup)
            if ceiling and not coil:
                enter = False
                blocked.append(
                    f"tested-ceiling veto: {ceiling['touches']}x-tested resistance "
                    f"₹{ceiling['price']:.2f} only {ceiling['dist_pct']:.2f}% overhead — "
                    f"wait for the break or a pullback"
                )
        # Pattern-quality gate (shared pattern AI engine): only trade good patterns.
        # Backtest/replay require an A-grade pattern; paper/live require ≥ B.
        if enter:
            try:
                from app.agents import get_pattern_engine
                from app.agents.pattern_engine import grade_rank
                session_mode = s.get("mode", "paper")
                # Exclude contaminated REPLAY memory from pattern scoring in replay
                # mode — same principle as the MemoryAgent fix.
                excl = {"REPLAY"} if session_mode == "replay" else None
                psig = await get_pattern_engine().signal(window, symbol,
                                                         exclude_memory_sources=excl)
                s["last_pattern"] = psig
                min_grade = gate.get("min_grade", _min_pattern_grade(session_mode))
                if psig.get("ok") and grade_rank(psig["grade"]) > grade_rank(min_grade):
                    enter = False
                    blocked.append(f"pattern grade {psig['grade']} below required {min_grade} "
                                   f"(model P(up) {psig.get('p_up')}, memory WR {psig.get('memory_winrate')})")
            except Exception as exc:
                logger.debug("pattern gate skipped: %s", exc)
        action = "BUY" if enter else "HOLD"
        if action == "BUY":
            reason = f"Entry: {buy_votes} agents voted BUY (ensemble {ens_action} {conf:.0%}). {reason}".strip()
        else:
            reason = f"No entry [{gate['label']} gate] — " + "; ".join(blocked) + "."
    else:  # LONG
        # Reward/risk fix (Mistake #3): winners were exiting at +0.7% avg despite a
        # +2.5% target — a soft ensemble SELL (mean-reversion noise) kept cutting
        # them early. So once a position is in profit, ONLY the technical
        # stop/target/trail (tsig) exits it; a soft ensemble SELL no longer does.
        # Flat/losing positions still exit on an ensemble SELL (cut losers fast).
        gain_pct = ((candle["close"] - entry_price) / entry_price * 100) if entry_price else 0.0
        ens_sell_strong = (ens_action == "SELL" and conf >= 0.78)
        # Grace debounce for ensemble-SELL exits (2026-07-07 AEGISVOPAK replay:
        # day_structure flipped BUY 0.75 → SELL 0.88 on the bar its support
        # broke, dumping the position 8 min in, within pennies of the dip low —
        # the stock recovered all afternoon). Inside the entry's grace window a
        # ONE-BAR ensemble flip is capitulation noise: require two consecutive
        # SELL bars before an ensemble exit. Stops/trend-break (tsig) are
        # untouched and still protect immediately.
        streak = (s["position"].get("ens_sell_streak", 0) + 1) if ens_action == "SELL" else 0
        s["position"]["ens_sell_streak"] = streak
        in_grace = held_minutes is not None and held_minutes < 10
        ens_sell_exit = ens_action == "SELL" and not (in_grace and streak < 2)
        if gain_pct >= 0.4 and not ens_sell_strong:
            action = "SELL" if tsig == -1 else "HOLD"      # winner — let it run
        else:
            action = "SELL" if (tsig == -1 or ens_sell_exit) else "HOLD"
        if action == "SELL":
            reason = f"Exit: intraday signal/ensemble {ens_action}. {reason}".strip()
        elif gain_pct >= 0.4:
            reason = (f"Letting winner run (+{gain_pct:.1f}%) — exit on stop/target/trail "
                      f"or a confident ensemble SELL only. Ensemble {ens_action} ({conf:.0%}).")
        else:
            reason = (f"Holding position — exit needs a sell-trigger (stop/target/trend-break) "
                      f"or the ensemble turning bearish. Ensemble {ens_action} ({conf:.0%}).")

    # ── Per-trade hold cap: force-exit any position held longer than the span ──
    # POLICY PARITY (2026-07-09): the validated exit policy is wide_hold60 —
    # hold_cap 60 is part of its DEFINITION, and every CF label / A/B number
    # assumes it. But sessions only enforced a cap when max_hold_minutes was
    # explicitly set (auto-watchlist), so manual paper/replay/backtest ran
    # UNLIMITED holds: 13 large-cap Jul-8 backtests lost -9.5% riding a slow
    # afternoon slide for 3-5h, where the labeled 60-min policy scores +0.4%
    # on identical entries. Default now comes from LIVE_POLICY.hold_cap;
    # an explicit max_hold_minutes still overrides (0 = unlimited, opt-in).
    max_hold = s.get("max_hold_minutes") or 0
    if max_hold <= 0:
        try:
            from app.agents.counterfactual import LIVE_POLICY
            max_hold = int(LIVE_POLICY.get("hold_cap", 60))
        except Exception:
            max_hold = 60
    if not force_close and pos_status == "LONG" and max_hold > 0 and action != "SELL":
        entry_m  = _time_to_minutes(pos.get("entry_time", candle.get("time", "09:15")))
        candle_m = _time_to_minutes(candle.get("time", "09:15"))
        held = candle_m - entry_m
        if held >= max_hold:
            action = "SELL"
            reason = f"Hold cap: position held {held}m ≥ {max_hold}m — force exit."

    # ── Late-session entry cutoff for replay/backtest ─────────────────────────
    # Afternoon entries are materially worse (14-15h win-rate 22-24% vs ~31% in
    # the morning). Paper has its own configurable no_entry_after; give replay and
    # backtest a fixed cutoff so historical training/eval reflects the same rule.
    if action == "BUY" and not force_close and s.get("mode") in ("replay", "backtest"):
        if _time_to_minutes(candle.get("time", "09:15")) >= _LATE_ENTRY_CUTOFF_MIN:
            action = "HOLD"
            reason = (f"No new entries after {_LATE_ENTRY_CUTOFF_MIN // 60:02d}:"
                      f"{_LATE_ENTRY_CUTOFF_MIN % 60:02d} — afternoon win-rate is materially lower.")

    # ── Paper-trading specific rules (configurable times + profit/drop logic) ──
    if not force_close and s.get("mode") == "paper":
        cfg          = await get_paper_config()
        candle_m     = _time_to_minutes(candle.get("time", "09:15"))
        no_entry_m   = _time_to_minutes(cfg.get("no_entry_after",  "14:00"))
        squareoff_m  = _time_to_minutes(cfg.get("squareoff_after", "14:30"))

        # Configurable square-off: force sell at or after squareoff time
        if pos_status == "LONG" and candle_m >= squareoff_m and action != "SELL":
            action = "SELL"
            reason = f"Square off at {cfg.get('squareoff_after', '14:30')} — active trading window closed."

        # Configurable entry cutoff: no new buys at or after no_entry time
        if action == "BUY" and candle_m >= no_entry_m:
            action = "HOLD"
            reason = f"No new entries after {cfg.get('no_entry_after', '14:00')}."

        # Drop pattern (only while still in a HOLD — don't override ATR stop/take above).
        # Requires _PAPER_DROP_CANDLES consecutive lower closes — filters real trend breaks
        # from normal intraday chop (3 candles was too sensitive; 6 is the threshold).
        # Profit booking was removed: the ATR-scaled take-profit in _tech_signal
        # (+2.5% min) now manages exits so winners can run instead of being capped at 0.6%.
        if pos_status == "LONG" and action == "HOLD":
            if len(window) >= _PAPER_DROP_CANDLES + 1:
                recent_closes = [c["close"] for c in window[-(_PAPER_DROP_CANDLES + 1):]]
                if all(recent_closes[i] > recent_closes[i + 1] for i in range(_PAPER_DROP_CANDLES)):
                    action = "SELL"
                    reason = f"Drop pattern: {_PAPER_DROP_CANDLES} consecutive lower closes — exiting to preserve capital."

    # ── No more entries after a trade is closed in paper mode ────────────────
    if action == "BUY" and s.get("no_more_entries"):
        action = "HOLD"
        reason = "Session trade complete — running for agent learning only."

    trade_executed = None

    if action == "BUY" and pos["status"] == "NONE":
        from app.utils.trade_costs import buy_fill
        fill = buy_fill(candle["close"])          # market buy fills above the close (slippage)
        qty = max(1, int(s["cash"] * 0.95 / fill))
        cost = qty * fill
        if cost <= s["cash"]:
            s["cash"] -= cost
            fp, regime = _entry_fingerprint(window)
            s["position"] = {
                "status": "LONG", "entry_price": fill, "quantity": qty,
                "entry_time": candle["time"], "current_pnl": 0.0,
                "entry_fp": fp, "entry_regime": regime,
                # Capture every agent's real vote + the ensemble confidence AT ENTRY
                # so the Orders execution trace shows the actual entry decision
                # (not the exit candle's numbers or a synthetic 5-agent stand-in).
                "entry_agents": {a["agent_name"]: a["action"] for a in (agents or []) if a.get("agent_name")},
                "entry_conf": conf,
            }
            trade_executed = {"action": "BUY", "price": fill, "quantity": qty, "pnl": None, "time": candle["time"]}
            # LLM shadow review of this entry's dossier — logged + persisted,
            # never acted on (paper/live only: replay must stay deterministic,
            # and back-filling verdicts on historical bars would be meaningless).
            if s.get("mode") == "paper":
                try:
                    from app.services.llm_entry_review import shadow_review_entry
                    shadow_review_entry(symbol, candle, agents or [], ind,
                                        gate.get("label", "?"), s)
                except Exception:
                    logger.debug("shadow review hook failed", exc_info=True)
            s["trades"].append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": fill, "quantity": qty,
                "confidence": int(conf * 100), "reason": reason,
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
                "agents": [{"agent": a.get("agent_name") or a.get("agent"),
                            "action": a.get("action"), "confidence": a.get("confidence"),
                            "weight": a.get("weight"), "reasoning": a.get("reasoning")}
                           for a in (agents or [])],
            })
            # Train the AI engine: record this entry as a prediction so the exit
            # outcome can update per-agent weights (fingerprint=None here — the
            # pattern-memory bank is fed separately with the correct BUY+regime case).
            try:
                from app.agents import get_learning, get_rl_agent
                decision.action = "BUY"
                decision.confidence = conf
                rl_state = None
                try:
                    rl_state = get_rl_agent().extract_state(window)
                except Exception:
                    logger.warning("session %s RL state extraction failed for %s; continuing without RL state",
                                   s.get("id"), symbol, exc_info=True)
                ctx = {"symbol": symbol, "capital": s["capital"], "position": "NONE", "session_id": s["id"]}
                await get_learning().store_prediction(decision, candle["time"], ctx, rl_state, None)
                s["position"]["prediction_id"] = decision.prediction_id
            except Exception as exc:
                logger.debug("session store_prediction skipped: %s", exc)

    elif action == "SELL" and pos["status"] == "LONG":
        from app.utils.trade_costs import sell_fill, charges
        qty   = pos["quantity"]
        entry = pos["entry_price"]                 # already includes buy slippage
        exit_fill = sell_fill(candle["close"])     # market sell fills below the close
        fees    = charges(entry, exit_fill, qty)   # brokerage + exchange + GST + STT
        revenue = qty * exit_fill - fees
        pnl     = revenue - qty * entry            # net P&L (slippage in fills + charges)
        pnl_pct = round(pnl / (qty * entry) * 100, 2) if entry > 0 else 0.0
        s["cash"] += revenue
        trade_executed = {"action": "SELL", "price": exit_fill, "quantity": qty, "pnl": round(pnl, 2), "time": candle["time"]}
        s["trades"].append({
            "time": candle["time"], "timestamp": candle.get("timestamp", 0),
            "action": "SELL", "price": exit_fill, "quantity": qty,
            "confidence": int(conf * 100), "reason": reason,
            "pnl": round(pnl, 2), "pnl_pct": pnl_pct, "candle_index": idx, "indicators": ind,
            "costs": fees,
            "agents": [{"agent": a.get("agent_name") or a.get("agent"),
                        "action": a.get("action"), "confidence": a.get("confidence"),
                        "weight": a.get("weight"), "reasoning": a.get("reasoning")}
                       for a in (agents or [])],
        })
        # Persist round-trip to Orders + teach the memory bank
        try:
            _open = pos.get("entry_time") or candle["time"]
            entry_dt = datetime.strptime(f"{s['date']} {_open}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            exit_dt  = datetime.strptime(f"{s['date']} {candle['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            dur = int((exit_dt - entry_dt).total_seconds() / 60)
            asyncio.create_task(_save_backtest_trades([_build_trade_record(
                symbol=symbol, action="BUY", entry_price=entry, exit_price=exit_fill,
                pnl_abs=round(pnl, 2), pnl_pct_decimal=pnl_pct / 100.0,
                timestamp_open=entry_dt.isoformat(), timestamp_close=exit_dt.isoformat(),
                duration_minutes=dur,
                agent_signals=(pos.get("entry_agents") or _derive_agent_signals("BUY", ind)),
                market_context={"regime": "intraday", "vwap": ind.get("vwap"), "rsi": ind.get("rsi"),
                                "session_mode": s["mode"], "session_id": s["id"]},
                confidence=pos.get("entry_conf", conf),
                trade_source=s.get("trade_source", "PAPER" if s["mode"] == "paper" else "REPLAY"),
            )]))
        except Exception:
            logger.warning("session %s failed to persist closed trade for %s to Orders (pnl=%s)",
                           s.get("id"), symbol, pnl, exc_info=True)
        # Teach the pattern-memory bank (BUY + regime) — net of costs
        asyncio.create_task(_feed_memory(symbol, pos.get("entry_fp"), pos.get("entry_regime", "unknown"),
                                         pnl_pct, entry, exit_fill, s["mode"]))
        # Train the AI engine: record the outcome → updates per-agent weights
        _pid = pos.get("prediction_id")
        if _pid:
            try:
                from app.agents import get_learning
                asyncio.create_task(get_learning().record_outcome(
                    _pid, symbol, entry, exit_fill, round(pnl, 2), pnl_pct))
            except Exception as exc:
                logger.debug("session record_outcome skipped: %s", exc)
        s["position"] = {"status": "NONE", "entry_price": 0.0, "quantity": 0, "entry_time": None, "current_pnl": 0.0}
        # Post-loss cooldown: block re-entry for a window after a losing round-trip
        # so the system doesn't immediately re-scalp the same chop range.
        if pnl < 0 and not force_close:
            s["cooldown_until_min"] = _time_to_minutes(candle.get("time", "09:15")) + _LOSS_COOLDOWN_MIN
        # Paper sessions: one trade per session — keep running for agent learning
        if s.get("mode") == "paper" and not force_close:
            s["no_more_entries"] = True

    # Unrealised P&L
    if s["position"]["status"] == "LONG":
        s["position"]["current_pnl"] = round(
            (candle["close"] - s["position"]["entry_price"]) * s["position"]["quantity"], 2)

    s["current_time"]   = candle["time"]
    # Equity = cash + open-position market value. _compute_metrics does
    # cash - capital, which reads an open LONG as a ~95% "loss" (the buy
    # debited cash but the holding was never counted). Invisible for weeks
    # because sessions never held positions; surfaced by the 2026-07-07
    # entry-policy fixes.
    equity = s["cash"]
    if s["position"]["status"] == "LONG":
        equity += s["position"]["quantity"] * candle["close"]
    s["metrics"]        = _compute_metrics(equity, s["capital"], s["trades"])
    s["agent_decision"] = {"action": action, "confidence": round(conf, 3), "reason": reason,
                           "trade_executed": trade_executed}
    s["agents"]         = agents
    s["updated_at"]     = datetime.now(IST).isoformat()

    # ── Live decision trace — the per-candle "why" the UI shows, and a rolling
    # log kept on the session for review + training. ──────────────────────────
    _timing_label = {1: "BUY trigger", -1: "SELL/exit trigger", 0: "no trigger"}.get(tsig, "—")
    decision = {
        "time": candle.get("time"),
        "price": round(candle["close"], 2),
        "action": action,
        "executed": trade_executed is not None,
        "trade": trade_executed,
        "position": pos_status,
        "reason": reason,
        "confidence": round(conf, 3),
        "timing_signal": tsig,
        "timing_label": _timing_label,
        "timing_mode": "aggressive" if aggressive else "normal",
        "ensemble_action": ens_action,
        "indicators": {
            "rsi": round(ind.get("rsi", 0.0), 1),
            "momentum_pct": round(ind.get("mom5", 0.0), 2),
            "vwap": round(ind.get("vwap", 0.0), 2),
            "sma5": round(ind.get("sma5", 0.0), 2),
            "sma20": round(ind.get("sma20", 0.0), 2),
            "atr": round(ind.get("atr", 0.0), 2),
        },
        "agents": [{"agent": a.get("agent_name") or a.get("agent"), "action": a.get("action"),
                    "confidence": a.get("confidence"), "weight": a.get("weight"),
                    "reasoning": a.get("reasoning")} for a in (agents or [])],
    }
    s["last_decision"] = decision
    log = s.get("decision_log") or []
    # one entry per candle — replace if we re-decided the same minute
    if log and log[-1].get("time") == decision["time"]:
        log[-1] = decision
    else:
        log.append(decision)
    s["decision_log"] = log[-30:]    # keep last 30 candles (~30 min) in Redis for the UI
    asyncio.create_task(_persist_session_decision(s["id"], symbol, decision))


# ── Advancement (called by the background loop) ───────────────────────────────

async def _advance_replay(s: dict) -> None:
    allc = s.get("all_candles") or []
    n = len(allc)
    if n == 0:
        s["status"] = "done"
        return
    idx = s.get("current_idx", 0)
    if idx >= n - 1:
        s["status"] = "done"
        return
    steps  = max(1, int(s.get("speed", 1)))
    target = min(idx + steps, n - 1)
    for j in range(idx + 1, target + 1):
        window = allc[: j + 1]
        await _step(s, window, force_close=(j == n - 1))
        s["current_idx"] = j
        await asyncio.sleep(0.05)  # 50ms yield between replay steps — lets HTTP requests through
    if s["current_idx"] >= n - 1:
        s["status"] = "done"


_PAPER_POLL_SECS = 8   # how often a paper session refreshes its live market data


async def _try_groww_ltp(symbol: str) -> float:
    """Best-effort real-time price from Groww. Returns 0.0 if the key lacks the
    live-data entitlement — swallowed quietly so it never triggers a token-refresh
    storm. Tries the lightweight real-time /live-data/ltp endpoint first (lowest
    latency, often entitled when full quote isn't), then falls back to /quote."""
    from app.utils.groww_client import get_groww_client
    from app.utils import groww_feed
    # 0. Live websocket stream (preferred — real-time ticks from the feed service)
    px = await groww_feed.get_ltp(symbol)
    if px > 0:
        return px
    # Make sure the feed service is streaming this symbol for next time.
    await groww_feed.request_symbols([symbol])

    groww = get_groww_client()
    if not groww or groww.get_status().get("status") != "ok":
        return 0.0
    # 1. Real-time LTP endpoint (current last-traded price, minimal payload)
    try:
        raw = await groww.get_ltp([symbol])
        for key in (f"NSE_{symbol}", symbol):
            entry = raw.get(key)
            if isinstance(entry, dict):
                v = entry.get("ltp") or entry.get("last_price") or entry.get("last_trade_price")
                if v:
                    return float(v)
            elif isinstance(entry, (int, float)) and entry:
                return float(entry)
    except Exception:
        # Best-effort/quiet by design (see docstring) — debug only to avoid log spam
        # when the key simply lacks the live-data entitlement.
        logger.debug("Groww LTP endpoint failed for %s", symbol, exc_info=True)
    # 2. Full quote fallback
    try:
        raw = await groww.get_quote(symbol)
        for k in ("ltp", "lastPrice", "last_price", "lastTradedPrice"):
            v = raw.get(k)
            if v:
                return float(v)
    except Exception:
        logger.debug("Groww quote fallback failed for %s", symbol, exc_info=True)
        return 0.0
    return 0.0


async def _advance_paper(s: dict) -> None:
    """Real-time-ish paper trading on live market data.

    Today's intraday comes from Yahoo Finance (real NSE prices, ~1–2 min delay) —
    Groww's API key here only has historical access (live quotes return 401, and
    Groww historical doesn't serve the in-progress day). We refresh every
    _PAPER_POLL_SECS, opportunistically overlay a real Groww tick if the live-data
    entitlement is present, and let the ensemble decide once per completed candle.
    """
    from app.api import paper_trading as pt

    mstatus = pt._market_status_label()
    symbol  = s["symbol"]
    now     = pt._now_ist()
    ts      = now.timestamp()
    cur     = pt._current_candle_time()
    next_m  = _time_to_minutes(cur) + 1
    ended   = next_m > _SQUAREOFF_MINUTES or mstatus != "open"

    # Throttle external fetches so we never hammer the data sources
    last_poll = float(s.get("_last_poll_ts") or 0)
    if (ts - last_poll) >= _PAPER_POLL_SECS or not s.get("candles"):
        s["_last_poll_ts"] = ts
        # Real-time LTP overlay, best source first:
        #   1. Angel One (broker feed, refreshed ~every 3s by the poll loop — no HTTP here)
        #   2. Groww live ticks (only if the key has the live-data entitlement)
        #   3. none → Yahoo's current-minute candle (already near real-time)
        ltp = 0.0
        src = "yahoo_live"
        primary = await pt._get_primary()
        # 1. Groww live websocket feed — a cheap Redis read, so always try it first
        #    (independent of the sticky _groww_live_off REST flag). Register the
        #    symbol so the feed service streams it.
        from app.utils import groww_feed
        if primary != "yahoo":
            await groww_feed.request_symbols([symbol])
            f_ltp = await groww_feed.get_ltp(symbol)
            if f_ltp > 0:
                ltp = f_ltp
                src = "groww_stream"
                pt._accumulate_tick(symbol, ltp, ts)
        # 2. Broker/REST overlays only if the stream has nothing yet.
        if ltp <= 0 and primary != "yahoo":
            from app.utils.angel_client import angel_get_ltp
            a_ltp = angel_get_ltp(symbol) if primary != "groww" else 0.0
            if a_ltp > 0:
                ltp = a_ltp
                src = "angel_live"
                pt._accumulate_tick(symbol, ltp, ts)
            elif not s.get("_groww_live_off"):
                ltp = await _try_groww_ltp(symbol)
                if ltp > 0:
                    src = "groww_live"
                    pt._accumulate_tick(symbol, ltp, ts)
                    s["_groww_live_fails"] = 0
                else:
                    fails = int(s.get("_groww_live_fails") or 0) + 1
                    s["_groww_live_fails"] = fails
                    if fails >= 3:
                        s["_groww_live_off"] = True   # no REST live-data → stream/Yahoo only
        # Refresh the Yahoo base every poll — _get_yahoo_cached has its own 15s TTL,
        # so this only hits Yahoo's API when the cache is stale. Without this the
        # base was fetched once per symbol and never again, freezing each session's
        # candle window at the time it started (stale data, no new candles).
        try:
            await pt._get_yahoo_cached(symbol, cur)
        except Exception:
            logger.warning("session %s Yahoo candle refresh failed for %s", s.get("id"), symbol, exc_info=True)
        # 3. Final fallback: Yahoo's real-time market price (meta.regularMarketPrice,
        #    refreshed by the chart fetch above). Keeps the live bar + P&L moving at
        #    ~15s freshness even with no Groww/Angel tick, instead of waiting for
        #    Yahoo's next completed 1-min candle (~1-2 min gap).
        if ltp <= 0:
            y_ltp = pt.get_yahoo_live_ltp(symbol)
            if y_ltp > 0:
                ltp = y_ltp
                src = "yahoo_live"
                pt._accumulate_tick(symbol, ltp, ts)
        candles = pt._get_merged_candles(symbol, ltp, ts)
        if candles:
            s["candles"]      = candles
            s["data_source"]  = src
            s["current_time"] = candles[-1].get("time", cur)

    candles = s.get("candles") or []

    # Refresh live unrealised P&L every tick
    if candles and s["position"]["status"] == "LONG":
        last_px = float(candles[-1]["close"])
        s["position"]["current_pnl"] = round(
            (last_px - s["position"]["entry_price"]) * s["position"]["quantity"], 2)

    # Decide/trade once per completed candle, or square off at close
    new_candle = cur > (s.get("last_decided") or "00:00")
    must_close = ended and s["position"]["status"] == "LONG"
    if candles and (new_candle or must_close):
        await _step(s, candles, force_close=ended)
        s["last_decided"] = cur

    s["updated_at"] = now.isoformat()
    if ended and s["position"]["status"] != "LONG":
        s["status"] = "done"


_MAX_SESSION_ERRORS = 3


async def _advance(s: dict) -> None:
    try:
        if s["mode"] in ("replay", "backtest"):
            await _advance_replay(s)
        else:
            await _advance_paper(s)
        s.pop("error", None)
        s.pop("error_count", None)
    except Exception as exc:
        error_count = s.get("error_count", 0) + 1
        s["error"] = str(exc)[:200]
        s["error_count"] = error_count
        logger.warning("session %s advance error (%d/%d): %s",
                       s.get("id"), error_count, _MAX_SESSION_ERRORS, exc)
        if error_count >= _MAX_SESSION_ERRORS:
            s["status"] = "error"
            logger.error("session %s halted after %d consecutive errors",
                         s.get("id"), error_count)

    # Persist summary to PostgreSQL the first time a session reaches a terminal state.
    # _finalize_session sets s["_finalized"]=True before we save_session, so subsequent
    # ticks skip it even if Redis still holds the session blob.
    if s.get("status") in ("done", "stopped", "error") and not s.get("_finalized"):
        await _finalize_session(s)

    await save_session(s)


# ── Background loop ───────────────────────────────────────────────────────────

# Sessions running longer than this are considered abandoned (browser closed etc.)
# Replay sessions: one trading day = 6.25h max. Paper sessions: max 8h.
_SESSION_MAX_AGE_HOURS = 8


async def _auto_stop_stale(s: dict) -> bool:
    """Return True and stop the session if it has been running too long.

    Prevents abandoned sessions (browser-closed, autopilot crashed mid-run)
    from burning CPU/memory in the background runner indefinitely.
    """
    created_at_str = s.get("created_at", "")
    if not created_at_str:
        return False
    try:
        created_at = datetime.fromisoformat(created_at_str)
        age_hours = (datetime.now(IST) - created_at).total_seconds() / 3600
        if age_hours > _SESSION_MAX_AGE_HOURS:
            s["status"] = "stopped"
            s["error"] = f"Auto-stopped: session ran for {age_hours:.1f}h (>{_SESSION_MAX_AGE_HOURS}h limit)"
            s["updated_at"] = datetime.now(IST).isoformat()
            await save_session(s)
            logger.info(
                "Auto-stopped stale session",
                extra={"log_type": "session_event", "event": "auto_stop",
                       "session_id": s.get("id"), "age_hours": round(age_hours, 1)},
            )
            return True
    except Exception:
        logger.warning("session %s auto-stop-stale check failed", s.get("id"), exc_info=True)
    return False


_RUNNER_LOCK_KEY = "live_sessions:runner_lock"
_RUNNER_LOCK_TTL = 30   # seconds — if the primary process crashes, secondary takes over


async def _acquire_runner_lock() -> bool:
    """Try to become the sole runner process using a Redis SET NX lock.
    Returns True if this process is now the primary runner."""
    try:
        from app.utils.redis_client import get_redis
        r = get_redis()
        import os
        token = f"{os.getpid()}"
        acquired = await r.set(_RUNNER_LOCK_KEY, token, nx=True, ex=_RUNNER_LOCK_TTL)
        if acquired:
            return True
        # Refresh if we already hold the lock (our PID is stored).
        current = await r.get(_RUNNER_LOCK_KEY)
        if current == token:
            await r.expire(_RUNNER_LOCK_KEY, _RUNNER_LOCK_TTL)
            return True
        return False
    except Exception:
        logger.warning("runner-lock acquisition failed (Redis unavailable?); proceeding as sole runner", exc_info=True)
        return True   # If Redis unavailable, proceed (single-instance assumption).


async def session_runner_loop() -> None:
    import os, random
    # Stagger startup slightly so two workers don't race for the lock simultaneously.
    await asyncio.sleep(8 + random.uniform(0, 4))
    logger.info("Live session runner started",
                extra={"log_type": "app_lifecycle", "event": "session_runner_started"})
    _idle_ticks = 0
    while True:
        try:
            # Acquire distributed lock so only one process/worker runs the loop.
            # In a multi-worker or session-runner-container setup the "losing"
            # worker backs off quietly — no duplicate advancement.
            if not await _acquire_runner_lock():
                await asyncio.sleep(_TICK_SECONDS)
                continue

            # list_running_sessions() now returns slim control blobs (no candle
            # arrays) — each ~5 KB instead of ~150 KB — saving several MB of
            # Redis I/O per tick under heavy backtest load.
            running = await list_running_sessions()

            if not running:
                _idle_ticks += 1
                sleep_secs = min(30, _TICK_SECONDS * (1 + _idle_ticks // 3))
                await asyncio.sleep(sleep_secs)
                continue

            _idle_ticks = 0
            for s_slim in running:
                # Stale-check only needs created_at — slim blob has it.
                if await _auto_stop_stale(s_slim):
                    continue
                # Replay/backtest sessions need all_candles to advance; load the
                # full blob (slim + candles) only for the session being processed.
                # Paper sessions don't use all_candles so the slim blob suffices.
                if s_slim.get("mode") in ("replay", "backtest"):
                    s = await get_session(s_slim["id"])
                    if not s:
                        continue
                else:
                    s = s_slim
                await _advance(s)
                await asyncio.sleep(0)  # yield between sessions

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("session runner error: %s", exc)
        await asyncio.sleep(_TICK_SECONDS)


async def _already_backtested(symbol: str, date: str) -> bool:
    """Return True if symbol has any recorded BACKTEST or REPLAY trade on `date`."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{settings.FEEDBACK_SERVICE_URL}/trades/exists", params={"symbol": symbol, "date": date})
            if r.status_code == 200:
                return r.json().get("exists", False)
    except Exception:
        logger.warning("feedback-service already-backtested check failed for %s/%s; defaulting to BACKTEST", symbol, date, exc_info=True)
    return False


# ── Endpoints ─────────────────────────────────────────────────────────────────

async def start_session(req: StartSessionRequest):
    symbol = req.symbol.upper()
    mode   = req.mode if req.mode in ("replay", "paper", "backtest") else "replay"
    speed  = req.speed if req.speed in SPEEDS else 1
    model  = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    sid    = uuid.uuid4().hex[:12]
    now    = datetime.now(IST)

    base = {
        "id": sid, "mode": mode, "symbol": symbol, "speed": speed, "model": model,
        "capital": req.capital, "cash": req.capital, "status": "running",
        "position": {"status": "NONE", "entry_price": 0.0, "quantity": 0, "entry_time": None, "current_pnl": 0.0},
        "trades": [], "agents": [], "agent_decision": {},
        "max_hold_minutes": req.max_hold_minutes,
        "timing_mode": req.timing_mode if req.timing_mode in ("normal", "aggressive") else "normal",
        "metrics": _compute_metrics(req.capital, req.capital, []),
        "trade_source": "PAPER" if mode == "paper" else "REPLAY",  # overridden below for backtest/replay
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }

    if mode in ("replay", "backtest"):
        if not req.date:
            raise HTTPException(400, "date is required for replay/backtest mode")
        try:
            req_date = datetime.strptime(req.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        # A trading day is backtestable once it's finished. That means any past
        # day, OR today *after the 15:30 IST close* — by then the tick-store holds
        # today's full session, so a same-day recording (which the Recordings page
        # marks "completed" post-close) can be replayed immediately rather than
        # forcing the user to wait until tomorrow.
        market_closed_today = (now.hour * 60 + now.minute) >= _MARKET_CLOSE_MINUTES
        if req_date > now.date() or (req_date == now.date() and not market_closed_today):
            raise HTTPException(400, "Replay needs a completed trading day (today is only "
                                     "available after the 15:30 IST close).")
        if req_date.weekday() >= 5:
            raise HTTPException(400, "Selected date is a weekend. Pick a weekday.")

        all_candles, candle_src = await _fetch_full_day_candles(symbol, req.date)
        if not all_candles:
            raise HTTPException(422, _no_real_intraday_msg(symbol, req.date))
        prev_date = _prev_trading_day(datetime.strptime(req.date, "%Y-%m-%d")).strftime("%Y-%m-%d")
        prev_day, _ = await _fetch_full_day_candles(symbol, prev_date)

        # Dynamically determine BACKTEST vs REPLAY:
        # first time trading this symbol on this date → BACKTEST, repeat → REPLAY
        already_run = await _already_backtested(symbol, req.date)
        mode = "replay" if already_run else "backtest"
        trade_source = "REPLAY" if already_run else "BACKTEST"
        base["mode"] = mode
        base["trade_source"] = trade_source

        start_idx = _idx_for_time(all_candles, req.start_time)
        base.update({
            "date": req.date, "all_candles": all_candles, "current_idx": start_idx,
            "prev_day_candles": prev_day, "prev_day_date": prev_date,
            "current_time": all_candles[start_idx]["time"], "data_source": candle_src,
        })
        # Initial step is handled by the background session_runner_loop within 2 s.
        # Running it here would block the HTTP response for 10–30 s (LLM ensemble).
    else:
        from app.api.paper_trading import (
            _market_status_label, _current_candle_time, _today_str, _fetch_candles_for_start,
        )
        mstatus = _market_status_label()
        if mstatus == "weekend":
            raise HTTPException(400, "Market is closed on weekends.")
        if mstatus == "pre_market":
            raise HTTPException(400, "Market hasn't opened yet (NSE opens 09:15 IST).")
        cur = _current_candle_time()
        candles, src = await _fetch_candles_for_start(symbol, cur)
        if not candles:
            raise HTTPException(503, f"No live candle data for {symbol} yet. Retry shortly.")
        base.update({
            "date": _today_str(), "candles": candles, "prev_day_candles": [],
            "current_time": cur, "data_source": src,
        })
        # NOTE: paper sessions no longer arm dataset capture. Recording which stocks
        # to capture into the 1-second dataset is now a dedicated, explicit feature
        # (see app/api/recordings.py) so the dataset only holds the stocks you chose
        # to record, not incidental paper-traded symbols.
        # Let the background loop handle the first step to avoid blocking the response.

    await save_session(base)
    logger.info("Live session started",
                extra={"log_type": "session_event", "event": "session_start",
                       "session_id": sid, "mode": mode, "symbol": symbol, "date": base.get("date")})
    return {"status": "success", "data": _detail(base)}


async def session_statuses():
    """Lightweight endpoint: returns {id → status} for all sessions.

    Used by the autopilot to monitor queue completion without loading full
    session summaries (~150 KB → ~500 bytes per response).
    Uses a Redis pipeline so all GETs are a single round trip.
    """
    from app.utils.session_store import list_ids
    from app.utils.redis_client import get_redis
    ids = await list_ids()
    if not ids:
        return {"status": "success", "data": {}}
    r = get_redis()
    pipe = r.pipeline()
    for sid in ids:
        pipe.get(f"live_session:{sid}")
    raws = await pipe.execute()
    out: dict[str, str] = {}
    import json as _json
    for sid, raw in zip(ids, raws):
        if raw:
            try:
                s = _json.loads(raw)
                out[sid] = s.get("status", "unknown")
            except Exception:
                logger.debug("Failed to parse session blob for %s in statuses listing", sid, exc_info=True)
    return {"status": "success", "data": out}


async def list_all_sessions(limit: int = 50, status: str | None = None):
    """Returns the most recent `limit` sessions (default 50, max 200).
    Optional ?status=running filter returns only sessions with that status.
    Uses slim Redis blobs — no candle arrays loaded."""
    limit = min(max(1, limit), 200)
    if status == "running":
        # Use the dedicated running-sessions index — O(running) not O(all)
        from app.utils.session_store import list_running_sessions
        sessions = await list_running_sessions()
        return {"status": "success", "data": [_summary(s) for s in sessions]}
    sessions = await list_sessions_slim(limit=limit)
    return {"status": "success", "data": [_summary(s) for s in sessions]}


# ── Paper trading time config (must be before /{session_id} to avoid shadowing)
class PaperConfigRequest(BaseModel):
    no_entry_after:  str
    squareoff_after: str


async def get_paper_trading_config():
    return {"status": "success", "data": await get_paper_config()}


async def set_paper_trading_config(req: PaperConfigRequest):
    import re
    hhmm = re.compile(r"^\d{2}:\d{2}$")
    if not hhmm.match(req.no_entry_after) or not hhmm.match(req.squareoff_after):
        raise HTTPException(400, "Times must be HH:MM format")
    prev = await get_paper_config()
    cfg = {"no_entry_after": req.no_entry_after, "squareoff_after": req.squareoff_after}
    await save_paper_config(cfg)
    if cfg != prev:
        try:
            from app.api.ai_engine import _log_system_event
            await _log_system_event(
                "Paper trading window changed", "trading",
                f"Entry cutoff {prev.get('no_entry_after')}→{cfg['no_entry_after']}, "
                f"square-off {prev.get('squareoff_after')}→{cfg['squareoff_after']}.",
            )
        except Exception:
            logger.warning("Failed to log paper-trading-window-changed system event", exc_info=True)
    return {"status": "success", "data": cfg}


async def get_one_session(session_id: str):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found (it may have expired).")
    return {"status": "success", "data": _detail(s)}


async def stop_session(session_id: str):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    if s.get("status") == "running":
        s["status"] = "stopped"
        s["updated_at"] = datetime.now(IST).isoformat()
        if not s.get("_finalized"):
            await _finalize_session(s)
        await save_session(s)
    return {"status": "success", "data": _summary(s)}


async def set_speed(session_id: str, req: SpeedRequest):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    s["speed"] = req.speed if req.speed in SPEEDS else 1
    await save_session(s)
    return {"status": "success", "data": _summary(s)}


async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"status": "success"}
