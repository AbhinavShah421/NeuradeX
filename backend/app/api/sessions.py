"""Live Trading Sessions — server-side, background-advancing trade sessions.

A session (AI Live Trading *replay* of a past day, or live *paper* trading) runs
on the server, not in the browser. State lives in Redis, a background loop
advances every running session candle-by-candle using the full 7-agent ensemble
(+ pattern memory), and the frontend simply reads state. This is what makes a
session survive a refresh, keep running in the background, run alongside others,
and be reopenable as a live chart.
"""
from __future__ import annotations
import asyncio
import httpx
import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.api.auth import get_current_user

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
router = APIRouter()

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
# A long entry requires genuine multi-agent support: at least this many agents
# must independently vote BUY, on every gate. Stops a single agent (or just the
# intraday timing trigger) from opening a trade the ensemble doesn't back.
_MIN_BUY_VOTES = 2

TRADE_GATES = {
    "strict": {"label": "Strict", "require_buy": True,  "min_conf": 0.52, "max_conf": 0.76,
               "desc": "Enters only when the ensemble votes BUY within the calibrated confidence band (over-confident signals are skipped). Fewest, best-calibrated trades."},
    "gentle": {"label": "Gentle", "require_buy": False, "min_conf": 0.44, "max_conf": 0.80,
               "desc": "Enters on an intraday setup the ensemble doesn't oppose, inside the calibrated confidence band. Balanced."},
    "loose":  {"label": "Loose",  "require_buy": False, "min_conf": 0.0,  "max_conf": 1.01,
               "desc": "Enters on any intraday setup the ensemble isn't bearish on, at any confidence. Most trades."},
}
_gate_cache = {"mode": "", "ts": 0.0}


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
_PAPER_CONFIG_DEFAULT = {"no_entry_after": "14:00", "squareoff_after": "14:30"}
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
        pass
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
        pass
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
        pass
    if mode not in TRADE_GATES:
        mode = "gentle"
    _gate_cache.update({"mode": mode, "ts": now})
    return mode


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
    """Run the 7-agent ensemble (+memory gate) on the candles seen so far."""
    from app.agents import get_engine, get_learning
    engine   = get_engine()
    learning = get_learning()
    try:
        weights = await learning.get_weights()
        if weights:
            engine.update_weights(weights)
    except Exception:
        pass
    context = {"symbol": symbol, "capital": capital, "position": position, "mode": mode,
               "date": date}  # date is YYYY-MM-DD for replay/backtest; None for paper
    decision = await engine.decide(symbol, candles, context)
    agents = [
        {"agent_name": a.agent_name, "action": a.action,
         "confidence": a.confidence, "weight": round(a.weight, 3),
         "reasoning": a.reasoning}
        for a in decision.agents
    ]
    return decision, agents


def _entry_fingerprint(candles: list[dict]):
    try:
        from app.agents.fingerprint import build_fingerprint, classify_regime
        return build_fingerprint(candles), classify_regime(candles)
    except Exception:
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


_PAPER_DROP_CANDLES = 6     # N consecutive lower closes = drop pattern (3 was triggering on normal chop)


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
    tsig = -1 if (force_close and pos_status == "LONG") else _tech_signal(ind, pos_status, candle, entry_price, aggressive=aggressive)

    # The 7-agent ensemble (+memory gate) is the decision brain: it provides the
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
        # Genuine BUY support: count agents that independently voted BUY.
        buy_votes = sum(1 for a in agents if (a.get("action") == "BUY"))
        if tsig != 1:
            blocked.append(_timing_block_reason(ind, candle))
        if buy_votes < _MIN_BUY_VOTES:
            blocked.append(f"only {buy_votes} agent{'s' if buy_votes != 1 else ''} voted BUY — need at least {_MIN_BUY_VOTES}")
        if gate["require_buy"] and ens_action != "BUY":
            blocked.append(f"ensemble did not vote BUY (it's {ens_action})")
        elif not gate["require_buy"] and ens_action == "SELL":
            blocked.append("ensemble is bearish (SELL)")
        max_conf = gate.get("max_conf", 1.01)
        if conf < gate["min_conf"]:
            blocked.append(f"confidence {conf:.0%} below the {gate['min_conf']:.0%} floor")
        if conf > max_conf:
            blocked.append(f"confidence {conf:.0%} above the {max_conf:.0%} ceiling (over-confident setups historically reverse)")
        enter = (tsig == 1
                 and buy_votes >= _MIN_BUY_VOTES
                 and (ens_action == "BUY" if gate["require_buy"] else ens_action != "SELL")
                 and gate["min_conf"] <= conf <= max_conf)
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
                min_grade = _min_pattern_grade(session_mode)
                if psig.get("ok") and grade_rank(psig["grade"]) > grade_rank(min_grade):
                    enter = False
                    blocked.append(f"pattern grade {psig['grade']} below required {min_grade} "
                                   f"(model P(up) {psig.get('p_up')}, memory WR {psig.get('memory_winrate')})")
            except Exception as exc:
                logger.debug("pattern gate skipped: %s", exc)
        action = "BUY" if enter else "HOLD"
        if action == "BUY":
            reason = f"Entry: intraday buy-trigger + ensemble {ens_action} ({conf:.0%}). {reason}".strip()
        else:
            reason = f"No entry [{gate['label']} gate] — " + "; ".join(blocked) + "."
    else:  # LONG
        # Exit on the intraday signal OR if the ensemble turns bearish; otherwise hold.
        action = "SELL" if (tsig == -1 or ens_action == "SELL") else "HOLD"
        if action == "SELL":
            reason = f"Exit: intraday signal/ensemble {ens_action}. {reason}".strip()
        else:
            reason = (f"Holding position — exit needs a sell-trigger (stop/target/trend-break) "
                      f"or the ensemble turning bearish. Ensemble {ens_action} ({conf:.0%}).")

    # ── Per-trade hold cap: force-exit any position held longer than the span ──
    # Applies to auto-traded watchlist stocks so no single trade overstays; the
    # stock keeps being traded for the rest of the session.
    max_hold = s.get("max_hold_minutes") or 0
    if not force_close and pos_status == "LONG" and max_hold > 0 and action != "SELL":
        entry_m  = _time_to_minutes(pos.get("entry_time", candle.get("time", "09:15")))
        candle_m = _time_to_minutes(candle.get("time", "09:15"))
        held = candle_m - entry_m
        if held >= max_hold:
            action = "SELL"
            reason = f"Hold cap: position held {held}m ≥ {max_hold}m — force exit."

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
            s["trades"].append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": fill, "quantity": qty,
                "confidence": int(conf * 100), "reason": reason,
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
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
                    pass
                ctx = {"symbol": symbol, "capital": s["capital"], "position": "NONE"}
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
            pass
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
        # Paper sessions: one trade per session — keep running for agent learning
        if s.get("mode") == "paper" and not force_close:
            s["no_more_entries"] = True

    # Unrealised P&L
    if s["position"]["status"] == "LONG":
        s["position"]["current_pnl"] = round(
            (candle["close"] - s["position"]["entry_price"]) * s["position"]["quantity"], 2)

    s["current_time"]   = candle["time"]
    s["metrics"]        = _compute_metrics(s["cash"], s["capital"], s["trades"])
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
                    "confidence": a.get("confidence")} for a in (agents or [])][:8],
    }
    s["last_decision"] = decision
    log = s.get("decision_log") or []
    # one entry per candle — replace if we re-decided the same minute
    if log and log[-1].get("time") == decision["time"]:
        log[-1] = decision
    else:
        log.append(decision)
    s["decision_log"] = log[-30:]    # keep last 30 candles (~30 min) — was 120, saves ~60 KB/session


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
    live-data entitlement (401) — we swallow it quietly so it never triggers a
    token-refresh storm. When/if the live-data subscription is enabled, paper
    sessions automatically start using real Groww ticks."""
    from app.utils.groww_client import get_groww_client
    groww = get_groww_client()
    if not groww or groww.get_status().get("status") != "ok":
        return 0.0
    try:
        raw = await groww.get_quote(symbol)
        for k in ("ltp", "lastPrice", "last_price", "lastTradedPrice"):
            v = raw.get(k)
            if v:
                return float(v)
    except Exception:
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
        # Honour the user's primary data-provider choice. 'yahoo' forces the
        # pure-Yahoo path (no broker overlays); 'groww' skips the Angel feed.
        primary = await pt._get_primary()
        if primary != "yahoo":
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
                        s["_groww_live_off"] = True   # no live-data → use Yahoo only
        # Refresh the Yahoo base every poll — _get_yahoo_cached has its own 15s TTL,
        # so this only hits Yahoo's API when the cache is stale. Without this the
        # base was fetched once per symbol and never again, freezing each session's
        # candle window at the time it started (stale data, no new candles).
        try:
            await pt._get_yahoo_cached(symbol, cur)
        except Exception:
            pass
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
        pass
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


_FEEDBACK_BASE = "http://feedback-service:8012"


async def _already_backtested(symbol: str, date: str) -> bool:
    """Return True if symbol has any recorded BACKTEST or REPLAY trade on `date`."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{_FEEDBACK_BASE}/trades/exists", params={"symbol": symbol, "date": date})
            if r.status_code == 200:
                return r.json().get("exists", False)
    except Exception:
        pass
    return False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start")
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
        if req_date >= now.date():
            raise HTTPException(400, "Replay needs a completed past trading day.")
        if req_date.weekday() >= 5:
            raise HTTPException(400, "Selected date is a weekend. Pick a weekday.")

        all_candles = await _fetch_full_day_candles(symbol, req.date)
        if not all_candles:
            raise HTTPException(422, _no_real_intraday_msg(symbol, req.date))
        prev_date = _prev_trading_day(datetime.strptime(req.date, "%Y-%m-%d")).strftime("%Y-%m-%d")
        prev_day  = await _fetch_full_day_candles(symbol, prev_date)

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
            "current_time": all_candles[start_idx]["time"], "data_source": "groww",
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
        # Let the background loop handle the first step to avoid blocking the response.

    await save_session(base)
    logger.info("Live session started",
                extra={"log_type": "session_event", "event": "session_start",
                       "session_id": sid, "mode": mode, "symbol": symbol, "date": base.get("date")})
    return {"status": "success", "data": _detail(base)}


@router.get("/statuses")
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
                pass
    return {"status": "success", "data": out}


@router.get("")
@router.get("/")
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


@router.get("/paper-config")
async def get_paper_trading_config():
    return {"status": "success", "data": await get_paper_config()}


@router.post("/paper-config")
async def set_paper_trading_config(req: PaperConfigRequest, user: dict = Depends(get_current_user)):
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
            pass
    return {"status": "success", "data": cfg}


@router.get("/{session_id}")
async def get_one_session(session_id: str):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found (it may have expired).")
    return {"status": "success", "data": _detail(s)}


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, user: dict = Depends(get_current_user)):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    if s.get("status") == "running":
        s["status"] = "stopped"
        s["updated_at"] = datetime.now(IST).isoformat()
        await save_session(s)
    return {"status": "success", "data": _summary(s)}


@router.post("/{session_id}/speed")
async def set_speed(session_id: str, req: SpeedRequest, user: dict = Depends(get_current_user)):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    s["speed"] = req.speed if req.speed in SPEEDS else 1
    await save_session(s)
    return {"status": "success", "data": _summary(s)}


@router.delete("/{session_id}")
async def remove_session(session_id: str, user: dict = Depends(get_current_user)):
    await delete_session(session_id)
    return {"status": "success"}
