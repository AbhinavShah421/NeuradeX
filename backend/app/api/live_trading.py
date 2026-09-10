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
from app.config import settings
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

# ── Broker reconciliation ──────────────────────────────────────────────────────
#
# `live:positions` only ever held trades WE placed through /place-order, so a
# trade placed by hand in the Groww app was invisible to every part of NeuradeX:
# not on this page, not on the dashboard, and — the part that actually costs
# money — not picked up by the auto-squareoff loop. Groww's RMS would flatten it
# at 3:20 PM and charge Rs59 for doing our job.
#
# Groww's own /positions/user is the only authority on what is really held, and
# nothing in this codebase had ever called it. So reconcile against it: adopt
# what we did not know about, and drop what the broker says is gone.

_MANUAL_SOURCE = "groww_manual"


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else default
    except (TypeError, ValueError):
        return default


def _map_broker_position(raw: dict) -> Optional[dict]:
    """Map one Groww position onto our shape, or None if it is not a live one.

    Deliberately tolerant about field NAMES and strict about field VALUES.
    Groww's position payload has never been exercised by this codebase — nothing
    had ever called /positions/user — so the exact spelling is unverified and
    several plausible ones are accepted. What is NOT guessed is the numbers: a
    position whose quantity or price cannot be read is returned as None rather
    than defaulted, because a fabricated quantity here becomes a real order.
    """
    if not isinstance(raw, dict):
        return None

    def pick(*names, default=None):
        for n in names:
            if raw.get(n) not in (None, ""):
                return raw[n]
        return default

    symbol = pick("trading_symbol", "tradingSymbol", "symbol", "tradingsymbol")
    if not symbol:
        return None
    symbol = str(symbol).upper()

    # Net quantity decides both existence and direction. Groww may report it as
    # a single net figure or as separate buy/sell legs.
    qty = pick("net_quantity", "netQuantity", "quantity", "net_qty")
    if qty is None:
        buy_q = _num(pick("buy_quantity", "buyQuantity", default=0))
        sell_q = _num(pick("sell_quantity", "sellQuantity", default=0))
        qty = buy_q - sell_q
    qty = _num(qty, default=0.0)
    if qty == 0:
        return None                      # flat — not an open position

    entry = _num(pick("average_price", "averagePrice", "avg_price", "buy_price",
                      "net_price", "price"), default=0.0)
    if entry <= 0:
        logger.warning(
            "Groww position for %s has no usable average price — skipped",
            symbol,
            extra={"log_type": "live_trading_event", "event": "adopt_no_price",
                   "symbol": symbol, "raw_keys": sorted(raw.keys())},
        )
        return None

    product = str(pick("product", "product_type", "productType", default="MIS")).upper()

    return {
        "symbol":       symbol,
        "action":       "LONG" if qty > 0 else "SHORT",
        "quantity":     abs(qty),
        "entry_price":  round(entry, 2),
        "entry_time":   str(pick("created_at", "createdAt", "order_time", default="")) or "—",
        "order_id":     str(pick("order_id", "orderId", default="groww-manual")),
        "confidence":   None,           # nobody scored this one
        "reason":       "Placed directly on Groww",
        "source":       _MANUAL_SOURCE,
        "product":      product,
    }


async def _broker_positions() -> tuple[list[dict], Optional[str]]:
    """(positions, error). An error is NOT an empty book — the caller must not
    read a failed fetch as 'the broker holds nothing' and start closing things."""
    groww = get_groww_client()
    if not groww:
        return [], "no groww client"
    try:
        raw = await groww.get_positions()
    except Exception as exc:
        logger.warning("Groww positions fetch failed: %s", exc)
        return [], str(exc)
    if not isinstance(raw, list):
        return [], f"unexpected payload type {type(raw).__name__}"

    out = []
    for r in raw:
        mapped = _map_broker_position(r)
        if mapped:
            out.append(mapped)
    return out, None


async def _reconcile_positions() -> dict:
    """Make `live:positions` agree with the broker. Returns a summary."""
    broker, err = await _broker_positions()
    if err:
        # Leave our book untouched. Dropping positions because a fetch failed
        # would un-manage real money on a network blip.
        return {"reconciled": False, "error": err}

    ours = await _get_positions()
    by_symbol = {p["symbol"]: p for p in ours}
    broker_symbols = {p["symbol"] for p in broker}

    adopted, dropped, updated = [], [], []
    merged: list[dict] = []

    for bp in broker:
        mine = by_symbol.get(bp["symbol"])
        if mine is None:
            merged.append(bp)
            adopted.append(bp["symbol"])
            logger.info(
                "Adopted a position placed outside NeuradeX",
                extra={"log_type": "live_trading_event", "event": "position_adopted",
                       "symbol": bp["symbol"], "quantity": bp["quantity"],
                       "entry_price": bp["entry_price"]},
            )
        else:
            # Keep OUR record (it carries the confidence and reason the broker
            # has no idea about) but take the broker's quantity, which is the
            # one that decides how much a square-off has to sell.
            if _num(mine.get("quantity")) != bp["quantity"]:
                updated.append(bp["symbol"])
                mine["quantity"] = bp["quantity"]
            merged.append(mine)

    for p in ours:
        if p["symbol"] not in broker_symbols:
            dropped.append(p["symbol"])
            logger.info(
                "Position closed outside NeuradeX — dropping from the live book",
                extra={"log_type": "live_trading_event", "event": "position_vanished",
                       "symbol": p["symbol"]},
            )

    if adopted or dropped or updated:
        await _save_positions(merged)

    return {"reconciled": True, "adopted": adopted, "dropped": dropped,
            "updated": updated, "open": len(merged)}


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
    # This is the endpoint the Live Trading page polls, so reconcile here too —
    # a trade placed in the Groww app should appear on the next poll rather than
    # whenever the background loop next happens to run.
    await _reconcile_positions()
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
    """Open live positions, reconciled against the broker first.

    Reconciling on read is what makes a trade placed in the Groww app show up
    here without waiting for the background loop — this endpoint backs the UI,
    so the refresh the user just did is the moment they expect to see it.
    """
    summary = await _reconcile_positions()
    return {"status": "success", "data": await _get_positions(), "reconcile": summary}


@router.post("/reconcile")
async def reconcile_now(user: dict = Depends(get_current_user)):
    """Force a broker reconciliation and report what changed."""
    return {"status": "success", "data": await _reconcile_positions()}


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

            enabled = await _is_enabled()
            # Reconcile FIRST. Without this the loop squares off only what we
            # placed, and a trade made in the Groww app — the exact case this
            # feature exists for — is left for the broker's RMS at 3:20 PM.
            if enabled:
                await _reconcile_positions()
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
                        # A short is flattened by BUYING it back. The hardcoded
                        # SELL here would have doubled a short instead of
                        # closing it — harmless while nothing shorted, and an
                        # adopted Groww position can be short.
                        await groww.place_order(
                            symbol           = pos["symbol"],
                            quantity         = int(_num(pos.get("quantity"), 0)),
                            transaction_type = "BUY" if pos.get("action") == "SHORT" else "SELL",
                            order_type       = "MARKET",
                            price            = 0.0,
                            product          = pos.get("product", "MIS"),
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


# ── Auto management ────────────────────────────────────────────────────────────

AUTO_MANAGE_SECS = 60      # one ensemble evaluation per position per minute


async def _exit_position(pos: dict, price: float, reason: str) -> bool:
    """Place the real MIS order that flattens `pos`, and record it. True if sent."""
    groww = get_groww_client()
    if not groww:
        logger.error("Cannot exit %s — no Groww client", pos["symbol"])
        return False

    qty = int(_num(pos.get("quantity"), 0))
    if qty <= 0:
        # Not a safety rail, a physical one: there is no order to place without
        # a quantity, and inventing one would sell something we may not hold.
        logger.error(
            "Cannot exit %s — quantity is %r",
            pos["symbol"], pos.get("quantity"),
            extra={"log_type": "live_trading_event", "event": "exit_no_qty",
                   "symbol": pos["symbol"]},
        )
        return False

    # A long is closed by selling; a short by buying back.
    side = "BUY" if pos.get("action") == "SHORT" else "SELL"
    try:
        await groww.place_order(
            symbol=pos["symbol"], quantity=qty, transaction_type=side,
            order_type="MARKET", price=0.0, product=pos.get("product", "MIS"),
            exchange="NSE",
        )
    except Exception as exc:
        logger.error(
            "Exit order FAILED for %s: %s", pos["symbol"], exc,
            extra={"log_type": "live_trading_event", "event": "exit_failed",
                   "symbol": pos["symbol"], "error": str(exc)},
        )
        return False

    entry = _num(pos.get("entry_price"))
    pnl = pnl_pct = None
    if entry > 0 and price > 0:
        per_share = (entry - price) if pos.get("action") == "SHORT" else (price - entry)
        pnl = round(per_share * qty, 2)
        pnl_pct = round(pnl / (entry * qty) * 100, 2)

    await _append_history({
        "symbol": pos["symbol"], "action": side, "quantity": qty,
        "exit_price": price or None, "exit_time": _now_ist().strftime("%H:%M"),
        "order_id": pos.get("order_id"), "pnl": pnl, "pnl_pct": pnl_pct,
        "confidence": pos.get("confidence"), "reason": reason,
        "source": pos.get("source"),
    })
    logger.info(
        "Auto-exit placed for %s (%s) — %s",
        pos["symbol"], pos.get("source") or "neuradex", reason,
        extra={"log_type": "live_trading_event", "event": "auto_exit",
               "symbol": pos["symbol"], "reason": reason, "pnl": pnl},
    )
    return True


async def _decide_exit(pos: dict) -> tuple[bool, str, float]:
    """Ask the same per-candle decision path the paper sessions use.

    Returns (should_exit, reason, last_price). A position it cannot evaluate is
    LEFT ALONE — an exit is a real order, and "no data" is not a sell signal.
    """
    from app.api.backtest import _intraday_indicators, _llm_decide, _tech_signal
    from app.api.paper_trading import _fetch_candles_for_start

    symbol = pos["symbol"]
    now = _now_ist()
    try:
        candles, src = await _fetch_candles_for_start(symbol, now.strftime("%H:%M"))
    except Exception as exc:
        logger.warning("Auto-manage: candle fetch failed for %s: %s", symbol, exc)
        return False, "", 0.0
    if not candles:
        logger.warning("Auto-manage: no candles for %s (%s) — holding", symbol, src)
        return False, "", 0.0

    idx = len(candles) - 1
    candle = candles[idx]
    price = _num(candle.get("close"))
    ind = _intraday_indicators(candles, idx)
    entry = _num(pos.get("entry_price"))
    unreal = (price - entry) * _num(pos.get("quantity")) if entry > 0 else 0.0

    dec = await _llm_decide(
        symbol, now.strftime("%Y-%m-%d"), candle, ind,
        "LONG", entry, unreal, 0.0,
        _tech_signal(ind, "LONG", candle, entry),
        candles[max(0, idx - 5):idx + 1],
        getattr(settings, "OLLAMA_MODEL", "llama3.1:8b"),
    )
    action = str(dec.get("action", "HOLD")).upper()
    if action == "SELL":
        return True, f"AI exit — {dec.get('reason', 'signal')}", price
    return False, "", price


async def _auto_manage_loop() -> None:
    """Every minute in market hours: reconcile, then let the AI manage what is open.

    This is what puts a hand-placed Groww trade under auto control. It runs
    whether or not the position originated here — an adopted trade is managed
    exactly like one we placed, which is what was asked for.
    """
    while True:
        try:
            await asyncio.sleep(AUTO_MANAGE_SECS)
            if _now_ist().weekday() >= 5 or not _is_market_open():
                continue
            if not await _is_enabled():
                continue

            await _reconcile_positions()

            if not await _is_auto_execute():
                continue                      # visible, but nothing acts on it

            for pos in await _get_positions():
                try:
                    should_exit, reason, price = await _decide_exit(pos)
                    if should_exit and await _exit_position(pos, price, reason):
                        await _save_positions(
                            [p for p in await _get_positions()
                             if p["symbol"] != pos["symbol"]]
                        )
                except Exception:
                    logger.exception("Auto-manage failed for %s", pos.get("symbol"))

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Auto-manage loop error: %s", exc)
