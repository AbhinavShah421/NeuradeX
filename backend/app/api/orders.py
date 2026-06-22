"""
Orders API — place, cancel, and list orders via Groww Trading API.
"""

import random
import httpx
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    quantity: int
    transaction_type: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: Optional[float] = None
    product: Literal["CNC", "INTRADAY"] = "CNC"
    exchange: str = "NSE"

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.upper()

    @field_validator("quantity")
    @classmethod
    def positive_qty(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("quantity must be positive")
        return v

    @field_validator("price")
    @classmethod
    def price_for_limit(cls, v: Optional[float], info) -> Optional[float]:
        if info.data.get("order_type") == "LIMIT" and (v is None or v <= 0):
            raise ValueError("price is required for LIMIT orders")
        return v


@router.post("/")
async def place_order(order: OrderRequest):
    """
    Place a buy or sell order via Groww.
    Falls back to a simulated confirmation if Groww is not configured.
    """
    client = get_groww_client()
    if client:
        try:
            logger.info(
                "Calling Groww place_order",
                extra={
                    "log_type": "groww_call",
                    "caller": "orders.place_order",
                    "method": "place_order",
                    "symbol": order.symbol,
                    "transaction_type": order.transaction_type,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "product": order.product,
                    "price": order.price,
                },
            )
            result = await client.place_order(
                symbol=order.symbol,
                quantity=order.quantity,
                transaction_type=order.transaction_type,
                order_type=order.order_type,
                price=order.price or 0.0,
                product=order.product,
                exchange=order.exchange,
            )
            order_id = result.get("order_id") or result.get("orderId") or str(random.randint(10**9, 10**10))
            logger.info(
                "Order placed via Groww",
                extra={
                    "log_type": "order_event",
                    "event": "order_placed",
                    "order_id": order_id,
                    "symbol": order.symbol,
                    "transaction_type": order.transaction_type,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "product": order.product,
                    "price": order.price,
                },
            )
            return {
                "status": "success",
                "data": {
                    "order_id": order_id,
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                    "transaction_type": order.transaction_type,
                    "order_type": order.order_type,
                    "product": order.product,
                    "exchange": order.exchange,
                    "price": order.price,
                    "status": result.get("status", "PLACED"),
                    "timestamp": datetime.now().isoformat(),
                },
            }
        except Exception as e:
            msg = str(e)
            logger.error(
                "Groww order placement failed",
                extra={
                    "log_type": "order_event",
                    "event": "order_failed",
                    "symbol": order.symbol,
                    "transaction_type": order.transaction_type,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "error": msg,
                },
            )
            # Translate Groww auth errors into an actionable message. Read access
            # (holdings) can work while the key still lacks trading authorization.
            if "401" in msg or "Unauthorized" in msg.lower():
                detail = ("Groww rejected the order (401 Unauthorized): your Groww API key is not "
                          "authorized to place orders. In Groww → API/TradingView access, enable the "
                          "Orders/trading scope for this key and complete today's TOTP approval, then retry. "
                          f"(Holdings read-access already works.) Groww said: {msg}")
            elif "403" in msg or "forbidden" in msg.lower():
                detail = ("Groww rejected the order (403 Forbidden): this API key lacks the trading "
                          f"entitlement. Enable order placement on the key and retry. Groww said: {msg}")
            else:
                detail = f"Order placement failed: {msg}"
            raise HTTPException(status_code=400, detail=detail)

    # Groww not configured — return simulated confirmation
    logger.warning(
        "Groww not configured; returning simulated order confirmation",
        extra={"log_type": "order_event", "event": "order_simulated", "symbol": order.symbol},
    )
    return {
        "status": "success",
        "data": {
            "order_id": str(random.randint(10**9, 10**10)),
            "symbol": order.symbol,
            "quantity": order.quantity,
            "transaction_type": order.transaction_type,
            "order_type": order.order_type,
            "product": order.product,
            "exchange": order.exchange,
            "price": order.price,
            "status": "SIMULATED",
            "timestamp": datetime.now().isoformat(),
        },
    }


@router.get("/")
async def list_orders():
    """Live order book from Groww (today's orders, newest first)."""
    client = get_groww_client()
    if not client:
        return {"status": "success", "data": []}
    try:
        raw = await client.get_orders()
        orders = []
        for o in raw:
            orders.append({
                "order_id":         o.get("groww_order_id") or o.get("order_id") or o.get("orderId"),
                "reference_id":     o.get("order_reference_id"),
                "symbol":           o.get("trading_symbol") or o.get("symbol"),
                "transaction_type": o.get("transaction_type"),
                "quantity":         o.get("quantity"),
                "filled_quantity":  o.get("filled_quantity"),
                "order_type":       o.get("order_type"),
                "product":          o.get("product"),
                "status":           o.get("order_status") or o.get("status"),
                "price":            o.get("price") or o.get("average_fill_price"),
                "exchange":         o.get("exchange"),
                "segment":          o.get("segment", "CASH"),
                "created_at":       o.get("created_at") or o.get("order_timestamp"),
            })
        return {"status": "success", "data": orders}
    except Exception as e:
        logger.warning("Groww order list failed: %s", e,
                       extra={"log_type": "order_event", "event": "order_list_failed", "error": str(e)})
        return {"status": "success", "data": [], "note": "Could not read live orders from Groww."}


class CancelRequest(BaseModel):
    order_id: str
    segment: str = "CASH"


@router.post("/cancel")
async def cancel_order(req: CancelRequest):
    """Cancel a pending Groww order by its order id."""
    client = get_groww_client()
    if not client:
        raise HTTPException(status_code=400, detail="Groww is not connected.")
    try:
        result = await client.cancel_order(req.order_id, req.segment)
        logger.info("Groww order cancelled",
                    extra={"log_type": "order_event", "event": "order_cancelled", "order_id": req.order_id})
        return {"status": "success", "data": result}
    except Exception as e:
        msg = str(e)
        logger.error("Groww order cancel failed",
                     extra={"log_type": "order_event", "event": "order_cancel_failed", "order_id": req.order_id, "error": msg})
        raise HTTPException(status_code=400, detail=f"Cancel failed: {msg}")


# ── Feedback-service proxy ────────────────────────────────────────────────────
# The browser cannot reach feedback-service:8012 directly (Docker-internal only).
# These endpoints proxy through the backend so the frontend can call /api/orders/feedback/*.

_FEEDBACK_BASE = "http://feedback-service:8012"

async def _feedback_get(path: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{_FEEDBACK_BASE}{path}", params=params, timeout=5.0)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Feedback service unavailable: {e}")


@router.get("/feedback/stats")
async def feedback_stats():
    return await _feedback_get("/stats")


@router.get("/feedback/trades")
async def feedback_trades(source: str = None, limit: int = 500):
    params: dict = {"limit": limit}
    if source:
        params["source"] = source
    return await _feedback_get("/trades", params=params)


@router.get("/feedback/agent-accuracy")
async def feedback_agent_accuracy(min_trades: int = 20):
    return await _feedback_get(f"/agent-accuracy?min_trades={min_trades}")


@router.get("/feedback/portfolio-metrics")
async def feedback_portfolio_metrics():
    return await _feedback_get("/portfolio-metrics")
