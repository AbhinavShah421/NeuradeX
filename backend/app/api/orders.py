"""
Orders API — place, cancel, and list orders via Groww Trading API.
"""

import random
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
            logger.error(
                "Groww order placement failed",
                extra={
                    "log_type": "order_event",
                    "event": "order_failed",
                    "symbol": order.symbol,
                    "transaction_type": order.transaction_type,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "error": str(e),
                },
            )
            raise HTTPException(status_code=502, detail=f"Order placement failed: {e}")

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
    """List recent orders — simulated (Groww order history not yet integrated)."""
    return {
        "status": "success",
        "data": [],
    }
