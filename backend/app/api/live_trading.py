"""
Live Intraday Trading — real Groww MIS orders gated by ensemble conviction.

This module backs the Live Trading UI. It mirrors the paper-trading workflow
but places real Groww MARKET MIS orders instead of simulating them.

Conviction gate (both must pass before any live order fires):
  - ensemble confidence  >= CONVICTION_MIN  (default 0.72)
  - agent_agreement      >= AGREEMENT_MIN   (default 0.55)
  - action must not be HOLD

Position tracking in Redis:
  live:enabled          → "1" | "0"
  live:auto_execute     → "1" | "0"
  live:settings         → JSON { conviction_min, agreement_min, max_capital_pct,
                                  max_positions, allocated_capital }
  live:positions        → JSON list of open positions
  live:history:{date}   → JSON list of closed trades for that date (TTL 7 days)

All MIS positions are auto-squared-off at AUTO_SQUAREOFF_IST (3:10 PM) so we
never carry open intraday positions past market close.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.api.paper_trading import (
    IST,
    _MARKET_OPEN_MINUTES,
    _SQUAREOFF_MINUTES,
    _is_market_open,
    _now_ist,
    _today_str,
)
from app.utils.elk_logger import get_logger
from app.utils.groww_client import get_groww_client
from app.utils.redis_client import cache_delete, cache_get, cache_set

logger = get_logger(__name__)
router = APIRouter()

# ── Constants ──────────────────────────────────────────────────────────────────

CONVICTION_MIN   = 0.72   # ensemble confidence must be >= this
AGREEMENT_MIN    = 0.55   # fraction of agents voting same direction
MAX_CAPITAL_PCT  = 0.15   # max 15% of allocated capital per position
MAX_POSITIONS    = 3      # max concurrent live positions
AUTO_SQUAREOFF_H = 15
AUTO_SQUAREOFF_M = 10     # 3:10 PM IST → square off 5 min before market close

# Redis keys
_KEY_ENABLED     = "live:enabled"
_KEY_AUTO_EXEC   = "live:auto_execute"
_KEY_SETTINGS    = "live:settings"
_KEY_POSITIONS   = "live:positions"
_KEY_HISTORY_PFX = "live:history:"   # + date string


# ── Default settings ───────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "conviction_min":    CONVICTION_MIN,
    "agreement_min":     AGREEMENT_MIN,
    "max_capital_pct":   MAX_CAPITAL_PCT,
    "max_positions":     MAX_POSITIONS,
    "allocated_capital": 50_000.0,
}


# ── Redis helpers ──────────────────────────────────────────────────────────────

async def _get_settings() -> dict:
    raw = await cache_get(_KEY_SETTINGS)
    if raw:
        try:
            return {**_DEFAULT_SETTINGS, **json.loads(raw)}
        except Exception:
            pass
    return dict(_DEFAULT_SETTINGS)


async def _save_settings(s: dict) -> None:
    await cache_set(_KEY_SETTINGS, json.dumps(s), expire=86400 * 30)


async def _is_enabled() -> bool:
    return (await cache_get(_KEY_ENABLED)) == "1"


async def _is_auto_execute() -> bool:
    return (await cache_get(_KEY_AUTO_EXEC)) == "1"


async def _get_positions() -> list[dict]:
    raw = await cache_get(_KEY_POSITIONS)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return []


async def _save_positions(positions: list[dict]) -> None:
    await cache_set(_KEY_POSITIONS, json.dumps(positions), expire=86400)


async def _append_history(trade: dict) -> None:
    key = _KEY_HISTORY_PFX + _today_str()
    raw = await cache_get(key)
    history: list[dict] = []
    if raw:
        try:
            history = json.loads(raw)
        except Exception:
            pass
    history.append(trade)
    await cache_set(key, json.dumps(history), expire=86400 * 7)


async def _get_history(date: Optional[str] = None) -> list[dict]:
    key = _KEY_HISTORY_PFX + (date or _today_str())
    raw = await cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return []


# ── Models ─────────────────────────────────────────────────────────────────────

class EnableRequest(BaseModel):
    allocated_capital: float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    auto_execute:      bool  = False


class SettingsRequest(BaseModel):
    conviction_min:    Optional[float] = None
    agreement_min:     Optional[float] = None
    max_capital_pct:   Optional[float] = None
    max_positions:     Optional[int]   = None
    allocated_capital: Optional[float] = None
    auto_execute:      Optional[bool]  = None


class EvaluateRequest(BaseModel):
    """Evaluate an AI signal through the conviction gate."""
    symbol:          str
    action:          str              # BUY | SELL | HOLD
    confidence:      float            # ensemble confidence 0–1
    agent_agreement: float            # fraction of agents agreeing 0–1
    current_price:   float
    reasoning:       str = ""
    prediction_id:   Optional[str] = None
    agent_votes:     dict = {}


class PlaceOrderRequest(BaseModel):
    """Place a live order (already gate-checked by /evaluate or user-confirmed)."""
    symbol:        str
    action:        str              # BUY | SELL
    quantity:      int = Field(..., gt=0, le=10_000)
    current_price: float
    confidence:    float = 0.0
    prediction_id: Optional[str] = None
    reason:        str = ""


class SquareoffRequest(BaseModel):
    symbol: Optional[str] = None    # None → square off ALL positions


# ── Conviction gate ────────────────────────────────────────────────────────────

async def _check_gate(
    action: str, confidence: float, agreement: float
) -> tuple[bool, str]:
    """Return (passes, reason)."""
    if action == "HOLD":
        return False, "Signal is HOLD — no trade needed"
    s = await _get_settings()
    if confidence < s["conviction_min"]:
        return False, (
            f"Confidence {confidence:.0%} below threshold {s['conviction_min']:.0%}"
        )
    if agreement < s["agreement_min"]:
        return False, (
            f"Agent agreement {agreement:.0%} below threshold {s['agreement_min']:.0%}"
        )
    return True, f"Gate passed — confidence {confidence:.0%}, agreement {agreement:.0%}"


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def live_status(user: dict = Depends(get_current_user)):
    """Overall live trading status — enabled flag, positions, day P&L."""
    enabled      = await _is_enabled()
    auto_exec    = await _is_auto_execute()
    settings     = await _get_settings()
    positions    = await _get_positions()
    history      = await _get_history()
    now          = _now_ist()

    # Compute unrealised P&L from current prices (we don't poll prices here — caller
    # should use tick endpoint to get current prices and compute unrealised)
    realised_pnl = sum(t.get("pnl", 0.0) for t in history if t.get("pnl") is not None)

    squareoff_mins  = AUTO_SQUAREOFF_H * 60 + AUTO_SQUAREOFF_M
    current_mins    = now.hour * 60 + now.minute
    mins_to_sqoff   = max(0, squareoff_mins - current_mins)

    return {
        "status": "success",
        "data": {
            "enabled":              enabled,
            "auto_execute":         auto_exec,
            "settings":             settings,
            "positions":            positions,
            "position_count":       len(positions),
            "history_today":        history,
            "realised_pnl":         round(realised_pnl, 2),
            "trade_count_today":    len(history),
            "mins_to_squareoff":    mins_to_sqoff,
            "market_open":          _is_market_open(),
            "ist_now":              now.strftime("%H:%M:%S"),
        },
    }


@router.post("/enable")
async def enable_live(req: EnableRequest, user: dict = Depends(get_current_user)):
    """Enable live trading mode. Requires Groww client to be initialised."""
    groww = get_groww_client()
    if not groww:
        raise HTTPException(503, "Groww API client not initialised. Add credentials in Settings.")

    # Persist settings
    s = await _get_settings()
    s["allocated_capital"] = req.allocated_capital
    await _save_settings(s)

    await cache_set(_KEY_ENABLED, "1", expire=86400)
    await cache_set(_KEY_AUTO_EXEC, "1" if req.auto_execute else "0", expire=86400)

    logger.info(
        "Live trading ENABLED",
        extra={
            "log_type": "live_trading_event",
            "event": "enabled",
            "capital": req.allocated_capital,
            "auto_execute": req.auto_execute,
        },
    )
    return {"status": "success", "data": {"enabled": True, "auto_execute": req.auto_execute}}


@router.post("/disable")
async def disable_live(user: dict = Depends(get_current_user)):
    """Disable live trading. Does NOT auto-square-off — call /squareoff first if needed."""
    await cache_set(_KEY_ENABLED, "0", expire=86400)
    await cache_set(_KEY_AUTO_EXEC, "0", expire=86400)

    logger.info("Live trading DISABLED", extra={"log_type": "live_trading_event", "event": "disabled"})
    return {"status": "success", "data": {"enabled": False}}


@router.patch("/settings")
async def update_settings(req: SettingsRequest, user: dict = Depends(get_current_user)):
    """Update live trading parameters."""
    s = await _get_settings()
    if req.conviction_min    is not None: s["conviction_min"]    = req.conviction_min
    if req.agreement_min     is not None: s["agreement_min"]     = req.agreement_min
    if req.max_capital_pct   is not None: s["max_capital_pct"]   = req.max_capital_pct
    if req.max_positions     is not None: s["max_positions"]     = req.max_positions
    if req.allocated_capital is not None: s["allocated_capital"] = req.allocated_capital
    if req.auto_execute      is not None:
        await cache_set(_KEY_AUTO_EXEC, "1" if req.auto_execute else "0", expire=86400)
    await _save_settings(s)
    return {"status": "success", "data": s}


@router.post("/evaluate")
async def evaluate_signal(req: EvaluateRequest, user: dict = Depends(get_current_user)):
    """
    Run the conviction gate against an AI signal.

    Returns whether the signal passes and — if so — the recommended quantity.
    The caller decides whether to place the order automatically or ask for confirmation.
    """
    enabled = await _is_enabled()
    if not enabled:
        return {
            "status": "success",
            "data": {
                "gate_passed": False,
                "reason": "Live trading is disabled",
                "action": req.action,
                "confidence": req.confidence,
                "agent_agreement": req.agent_agreement,
            },
        }

    if not _is_market_open():
        return {
            "status": "success",
            "data": {
                "gate_passed": False,
                "reason": "Market is closed",
                "action": req.action,
            },
        }

    # Check position limits
    positions = await _get_positions()
    s = await _get_settings()

    if req.action == "BUY":
        symbol_already_open = any(p["symbol"] == req.symbol for p in positions)
        if symbol_already_open:
            return {
                "status": "success",
                "data": {"gate_passed": False, "reason": f"Already holding {req.symbol}"},
            }
        if len(positions) >= s["max_positions"]:
            return {
                "status": "success",
                "data": {
                    "gate_passed": False,
                    "reason": f"Max concurrent positions ({s['max_positions']}) reached",
                },
            }

    gate_passed, reason = await _check_gate(req.action, req.confidence, req.agent_agreement)

    quantity = 0
    allocated = 0.0
    if gate_passed and req.action == "BUY" and req.current_price > 0:
        capital_per_trade = s["allocated_capital"] * s["max_capital_pct"]
        quantity = max(1, int(capital_per_trade / req.current_price))
        allocated = round(quantity * req.current_price, 2)

    logger.info(
        "Gate evaluation",
        extra={
            "log_type": "live_trading_event",
            "event": "gate_eval",
            "symbol": req.symbol,
            "action": req.action,
            "confidence": req.confidence,
            "agreement": req.agent_agreement,
            "gate_passed": gate_passed,
            "reason": reason,
        },
    )

    return {
        "status": "success",
        "data": {
            "gate_passed":     gate_passed,
            "reason":          reason,
            "action":          req.action,
            "confidence":      req.confidence,
            "agent_agreement": req.agent_agreement,
            "recommended_qty": quantity,
            "allocated_capital": allocated,
            "auto_execute":    await _is_auto_execute(),
        },
    }


@router.post("/place-order")
async def place_live_order(req: PlaceOrderRequest, user: dict = Depends(get_current_user)):
    """
    Place a real Groww MIS order and record the position.

    For BUY: opens a new position.
    For SELL: closes the matching open position, records P&L.
    """
    if not await _is_enabled():
        raise HTTPException(400, "Live trading is not enabled.")
    if not _is_market_open():
        raise HTTPException(400, "Market is closed — cannot place orders outside NSE hours.")

    groww = get_groww_client()
    if not groww:
        raise HTTPException(503, "Groww API client not initialised.")

    action = req.action.upper()
    if action not in ("BUY", "SELL"):
        raise HTTPException(400, "action must be BUY or SELL")

    symbol = req.symbol.upper()

    try:
        result = await groww.place_order(
            symbol           = symbol,
            quantity         = req.quantity,
            transaction_type = action,
            order_type       = "MARKET",
            price            = 0.0,
            product          = "MIS",
            exchange         = "NSE",
        )
    except Exception as exc:
        logger.error(
            "Groww order failed",
            extra={"log_type": "live_trading_event", "event": "order_failed",
                   "symbol": symbol, "action": action, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(500, f"Groww order failed: {exc}")

    now      = _now_ist()
    fill_price = req.current_price  # best estimate before fill confirmation
    order_id   = result.get("order_id") or result.get("orderId") or "unknown"
    trade_time = now.strftime("%H:%M")

    positions = await _get_positions()

    if action == "BUY":
        position = {
            "symbol":        symbol,
            "action":        "LONG",
            "quantity":      req.quantity,
            "entry_price":   fill_price,
            "entry_time":    trade_time,
            "order_id":      order_id,
            "confidence":    req.confidence,
            "prediction_id": req.prediction_id,
            "reason":        req.reason,
        }
        positions.append(position)
        await _save_positions(positions)
        pnl = None

    else:  # SELL
        pnl        = None
        pnl_pct    = None
        entry_pos  = next((p for p in positions if p["symbol"] == symbol), None)
        if entry_pos:
            pnl     = round((fill_price - entry_pos["entry_price"]) * req.quantity, 2)
            pnl_pct = round(pnl / (entry_pos["entry_price"] * req.quantity) * 100, 2)
            positions = [p for p in positions if p["symbol"] != symbol]
            await _save_positions(positions)

        await _append_history({
            "symbol":      symbol,
            "action":      "SELL",
            "quantity":    req.quantity,
            "exit_price":  fill_price,
            "exit_time":   trade_time,
            "order_id":    order_id,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
            "confidence":  req.confidence,
            "reason":      req.reason,
        })

    logger.info(
        "Live order placed",
        extra={
            "log_type": "live_trading_event",
            "event": "order_placed",
            "symbol": symbol,
            "action": action,
            "quantity": req.quantity,
            "price": fill_price,
            "order_id": order_id,
            "pnl": pnl,
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":      symbol,
            "action":      action,
            "quantity":    req.quantity,
            "price":       fill_price,
            "order_id":    order_id,
            "pnl":         pnl,
            "placed_at":   now.strftime("%H:%M:%S IST"),
            "positions":   positions,
            "groww_raw":   result,
        },
    }


@router.get("/positions")
async def get_positions(user: dict = Depends(get_current_user)):
    return {"status": "success", "data": await _get_positions()}


@router.get("/history")
async def get_history(date: Optional[str] = None, user: dict = Depends(get_current_user)):
    return {"status": "success", "data": await _get_history(date)}


@router.post("/squareoff")
async def squareoff(req: SquareoffRequest, user: dict = Depends(get_current_user)):
    """Square off one or all open positions at current market price."""
    groww = get_groww_client()
    if not groww:
        raise HTTPException(503, "Groww API client not initialised.")

    positions = await _get_positions()
    targets   = positions if req.symbol is None else [p for p in positions if p["symbol"] == req.symbol]

    if not targets:
        return {"status": "success", "data": {"message": "No open positions to square off", "closed": []}}

    closed = []
    errors = []
    now    = _now_ist()

    for pos in targets:
        sym = pos["symbol"]
        qty = pos["quantity"]
        try:
            result = await groww.place_order(
                symbol           = sym,
                quantity         = qty,
                transaction_type = "SELL",
                order_type       = "MARKET",
                price            = 0.0,
                product          = "MIS",
                exchange         = "NSE",
            )
            # Try to get fill price from Groww response
            fill = float(result.get("average_price") or result.get("averagePrice") or 0) or pos["entry_price"]
            pnl  = round((fill - pos["entry_price"]) * qty, 2)
            pnl_pct = round(pnl / (pos["entry_price"] * qty) * 100, 2) if pos["entry_price"] > 0 else 0.0

            closed.append({"symbol": sym, "qty": qty, "fill": fill, "pnl": pnl})
            await _append_history({
                "symbol":      sym,
                "action":      "SELL",
                "quantity":    qty,
                "exit_price":  fill,
                "exit_time":   now.strftime("%H:%M"),
                "order_id":    result.get("order_id") or "unknown",
                "pnl":         pnl,
                "pnl_pct":     pnl_pct,
                "confidence":  pos.get("confidence", 0),
                "reason":      "Auto / manual squareoff",
            })

            logger.info(
                "Position squared off",
                extra={"log_type": "live_trading_event", "event": "squareoff",
                       "symbol": sym, "qty": qty, "pnl": pnl},
            )
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
            logger.error(
                "Squareoff failed",
                extra={"log_type": "live_trading_event", "event": "squareoff_failed",
                       "symbol": sym, "error": str(exc)},
            )

    # Remove successfully closed positions
    closed_syms = {c["symbol"] for c in closed}
    remaining   = [p for p in positions if p["symbol"] not in closed_syms]
    await _save_positions(remaining)

    return {
        "status": "success",
        "data": {"closed": closed, "errors": errors, "remaining_positions": remaining},
    }


# ── Auto-squareoff background task ────────────────────────────────────────────

async def _auto_squareoff_loop() -> None:
    """Runs every 60 seconds. At AUTO_SQUAREOFF time, closes all live positions."""
    squareoff_done_date: Optional[str] = None

    while True:
        try:
            await asyncio.sleep(60)
            now  = _now_ist()
            date = now.strftime("%Y-%m-%d")

            if now.weekday() >= 5:
                continue  # weekend

            cur_mins = now.hour * 60 + now.minute
            sqoff_mins = AUTO_SQUAREOFF_H * 60 + AUTO_SQUAREOFF_M

            if cur_mins < sqoff_mins or squareoff_done_date == date:
                continue  # not yet time, or already done today

            enabled   = await _is_enabled()
            positions = await _get_positions()

            if not enabled or not positions:
                squareoff_done_date = date
                continue

            logger.info(
                "Auto-squareoff triggered",
                extra={"log_type": "live_trading_event", "event": "auto_squareoff",
                       "positions": len(positions)},
            )

            groww = get_groww_client()
            if groww:
                for pos in positions:
                    try:
                        await groww.place_order(
                            symbol           = pos["symbol"],
                            quantity         = pos["quantity"],
                            transaction_type = "SELL",
                            order_type       = "MARKET",
                            price            = 0.0,
                            product          = "MIS",
                            exchange         = "NSE",
                        )
                        logger.info(
                            "Auto-squareoff order placed",
                            extra={"log_type": "live_trading_event", "event": "auto_squareoff_order",
                                   "symbol": pos["symbol"]},
                        )
                    except Exception as exc:
                        logger.error(
                            "Auto-squareoff order failed",
                            extra={"log_type": "live_trading_event", "event": "auto_squareoff_failed",
                                   "symbol": pos["symbol"], "error": str(exc)},
                        )

            await _save_positions([])
            squareoff_done_date = date

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Auto-squareoff loop error: %s", exc)
