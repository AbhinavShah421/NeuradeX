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
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.config import settings
from app.utils.elk_logger import get_logger
from app.utils.session_store import (
    save_session, get_session, delete_session, list_sessions,
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

_TICK_SECONDS = 2          # background loop cadence
SPEEDS        = (1, 2, 5, 10)

# ── Trade gate ────────────────────────────────────────────────────────────────
# How selective session entries are; switchable at runtime from the dashboard.
TRADE_GATE_KEY = "ai_engine:trade_gate"
TRADE_GATES = {
    "strict": {"label": "Strict", "require_buy": True,  "min_conf": 0.62,
               "desc": "Only enters when the full 7-agent ensemble votes BUY. Fewest, highest-conviction trades."},
    "gentle": {"label": "Gentle", "require_buy": False, "min_conf": 0.55,
               "desc": "Enters on an intraday setup the ensemble doesn't oppose, above a confidence floor. Balanced."},
    "loose":  {"label": "Loose",  "require_buy": False, "min_conf": 0.0,
               "desc": "Enters on any intraday setup the ensemble isn't bearish on. Most trades."},
}
_gate_cache = {"mode": "", "ts": 0.0}


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
        "data_source":    s.get("data_source"),
        "agent_action":   s.get("agent_decision", {}).get("action"),
        "agent_confidence": s.get("agent_decision", {}).get("confidence"),
        "created_at":     s.get("created_at"),
        "updated_at":     s.get("updated_at"),
        "error":          s.get("error"),
    }


def _detail(s: dict) -> dict:
    """Full view for the live chart — candles up to the current point only."""
    if s["mode"] == "replay":
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
        "agents":           s.get("agents", []),
        "model_used":       s.get("model"),
    }


async def _ensemble_decision(symbol: str, candles: list[dict], capital: float, position: str):
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
    context = {"symbol": symbol, "capital": capital, "position": position}
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
            source="PAPER" if mode == "paper" else "REPLAY",
        )
    except Exception as exc:
        logger.debug("session memory feed skipped: %s", exc)


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
    tsig = -1 if (force_close and pos_status == "LONG") else _tech_signal(ind, pos_status, candle, entry_price)

    # The 7-agent ensemble (+memory gate) is the decision brain: it provides the
    # confidence, the reasoning, and can confirm or veto what the timing signal proposes.
    agents = s.get("agents", [])
    conf   = 0.6
    reason = ""
    if not (force_close and pos_status == "LONG"):
        decision, agents = await _ensemble_decision(symbol, window, s["capital"], pos_status)
        conf, reason, ens_action = decision.confidence, decision.reasoning, decision.action
    else:
        ens_action = "SELL"

    # Combine timing signal + ensemble verdict into the final action
    if force_close and pos_status == "LONG":
        action, conf, reason = "SELL", 0.99, "Session end — squared off automatically."
    elif pos_status == "NONE":
        # Entry is governed by the selected trade gate (strict / gentle / loose).
        gate = TRADE_GATES[await get_trade_gate()]
        if gate["require_buy"]:
            enter = tsig == 1 and ens_action == "BUY" and conf >= gate["min_conf"]
        else:
            enter = tsig == 1 and ens_action != "SELL" and conf >= gate["min_conf"]
        action = "BUY" if enter else "HOLD"
        if action == "BUY":
            reason = f"Entry: intraday signal + ensemble {ens_action} ({conf:.0%}). {reason}".strip()
    else:  # LONG
        # Exit on the intraday signal OR if the ensemble turns bearish
        action = "SELL" if (tsig == -1 or ens_action == "SELL") else "HOLD"
        if action == "SELL":
            reason = f"Exit: intraday signal/ensemble {ens_action}. {reason}".strip()

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
                duration_minutes=dur, agent_signals=_derive_agent_signals("BUY", ind),
                market_context={"regime": "intraday", "vwap": ind.get("vwap"), "rsi": ind.get("rsi"),
                                "session_mode": s["mode"], "session_id": s["id"]},
                confidence=conf,
                trade_source="PAPER" if s["mode"] == "paper" else "REPLAY",
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
        # Try Groww live ticks, but give up after a few failures so a key without
        # the live-data entitlement doesn't keep churning token refreshes.
        ltp = 0.0
        if not s.get("_groww_live_off"):
            ltp = await _try_groww_ltp(symbol)
            if ltp > 0:
                pt._accumulate_tick(symbol, ltp, ts)
                s["_groww_live_fails"] = 0
            else:
                fails = int(s.get("_groww_live_fails") or 0) + 1
                s["_groww_live_fails"] = fails
                if fails >= 3:
                    s["_groww_live_off"] = True   # no live-data → use Yahoo only
        if symbol not in pt._yahoo_candles:
            try:
                await pt._get_yahoo_cached(symbol, cur)
            except Exception:
                pass
        candles = pt._get_merged_candles(symbol, ltp, ts)
        if candles:
            s["candles"]      = candles
            s["data_source"]  = "groww_live" if ltp > 0 else "yahoo_live"
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


async def _advance(s: dict) -> None:
    try:
        if s["mode"] == "replay":
            await _advance_replay(s)
        else:
            await _advance_paper(s)
    except Exception as exc:
        logger.warning("session %s advance error: %s", s.get("id"), exc)
        s["error"] = str(exc)[:200]
    await save_session(s)


# ── Background loop ───────────────────────────────────────────────────────────

async def session_runner_loop() -> None:
    await asyncio.sleep(8)
    logger.info("Live session runner started",
                extra={"log_type": "app_lifecycle", "event": "session_runner_started"})
    while True:
        try:
            for s in await list_sessions():
                if s.get("status") == "running":
                    await _advance(s)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("session runner error: %s", exc)
        await asyncio.sleep(_TICK_SECONDS)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_session(req: StartSessionRequest):
    symbol = req.symbol.upper()
    mode   = req.mode if req.mode in ("replay", "paper") else "replay"
    speed  = req.speed if req.speed in SPEEDS else 1
    model  = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    sid    = uuid.uuid4().hex[:12]
    now    = datetime.now(IST)

    base = {
        "id": sid, "mode": mode, "symbol": symbol, "speed": speed, "model": model,
        "capital": req.capital, "cash": req.capital, "status": "running",
        "position": {"status": "NONE", "entry_price": 0.0, "quantity": 0, "entry_time": None, "current_pnl": 0.0},
        "trades": [], "agents": [], "agent_decision": {},
        "metrics": _compute_metrics(req.capital, req.capital, []),
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }

    if mode == "replay":
        if not req.date:
            raise HTTPException(400, "date is required for replay mode")
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

        start_idx = _idx_for_time(all_candles, req.start_time)
        base.update({
            "date": req.date, "all_candles": all_candles, "current_idx": start_idx,
            "prev_day_candles": prev_day, "prev_day_date": prev_date,
            "current_time": all_candles[start_idx]["time"], "data_source": "groww",
        })
        # Run the initial candle's decision
        await _step(base, all_candles[: start_idx + 1], force_close=(start_idx == len(all_candles) - 1))
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
        await _step(base, candles, force_close=False)

    await save_session(base)
    logger.info("Live session started",
                extra={"log_type": "session_event", "event": "session_start",
                       "session_id": sid, "mode": mode, "symbol": symbol, "date": base.get("date")})
    return {"status": "success", "data": _detail(base)}


@router.get("")
@router.get("/")
async def list_all_sessions():
    sessions = await list_sessions()
    return {"status": "success", "data": [_summary(s) for s in sessions]}


@router.get("/{session_id}")
async def get_one_session(session_id: str):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found (it may have expired).")
    return {"status": "success", "data": _detail(s)}


@router.post("/{session_id}/stop")
async def stop_session(session_id: str):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    if s.get("status") == "running":
        s["status"] = "stopped"
        s["updated_at"] = datetime.now(IST).isoformat()
        await save_session(s)
    return {"status": "success", "data": _summary(s)}


@router.post("/{session_id}/speed")
async def set_speed(session_id: str, req: SpeedRequest):
    s = await get_session(session_id)
    if not s:
        raise HTTPException(404, "Session not found.")
    s["speed"] = req.speed if req.speed in SPEEDS else 1
    await save_session(s)
    return {"status": "success", "data": _summary(s)}


@router.delete("/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"status": "success"}
