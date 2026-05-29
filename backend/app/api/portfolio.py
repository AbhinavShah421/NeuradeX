"""
Portfolio API Routes — backed by Groww holdings/positions with simulation fallback.
"""

import random
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Fallback data — user's real Groww holdings approximated from last known weights.
# Used when the Groww API session is not yet TOTP-approved.
SIM_HOLDINGS = [
    {"symbol": "TMCV",       "quantity": 100, "average_price": 340.0,  "current_price": 378.0},
    {"symbol": "TMPV",       "quantity": 100, "average_price": 320.0,  "current_price": 356.0},
    {"symbol": "SBIN",       "quantity": 23,  "average_price": 770.0,  "current_price": 830.0},
    {"symbol": "INDUSINDBK", "quantity": 11,  "average_price": 800.0,  "current_price": 870.0},
    {"symbol": "PNB",        "quantity": 72,  "average_price": 90.0,   "current_price": 100.0},
    {"symbol": "FEDERALBNK", "quantity": 46,  "average_price": 170.0,  "current_price": 185.0},
    {"symbol": "IREDA",      "quantity": 36,  "average_price": 160.0,  "current_price": 175.0},
    {"symbol": "JKTYRE",     "quantity": 19,  "average_price": 370.0,  "current_price": 400.0},
    {"symbol": "ZEEL",       "quantity": 33,  "average_price": 125.0,  "current_price": 138.0},
    {"symbol": "IOB",        "quantity": 44,  "average_price": 48.0,   "current_price": 55.0},
    {"symbol": "SUZLON",     "quantity": 47,  "average_price": 50.0,   "current_price": 58.0},
    {"symbol": "IDBI",       "quantity": 20,  "average_price": 65.0,   "current_price": 72.0},
    {"symbol": "SYNCOMF",    "quantity": 69,  "average_price": 8.0,    "current_price": 10.0},
    {"symbol": "SHREEGANES", "quantity": 30,  "average_price": 8.0,    "current_price": 10.0},
    {"symbol": "VIKASECO",   "quantity": 36,  "average_price": 1.5,    "current_price": 2.0},
    {"symbol": "TRIVENIENT", "quantity": 44,  "average_price": 1.5,    "current_price": 2.0},
    {"symbol": "CROISSANCE", "quantity": 7,   "average_price": 1.5,    "current_price": 2.0},
]


class Alert(BaseModel):
    symbol: str
    alert_type: str
    condition: str
    enabled: bool


def _build_portfolio(holdings: list, ltp_map: dict) -> dict:
    """
    Build portfolio dict from Groww holdings + a symbol→LTP map.
    Groww holdings endpoint returns only: trading_symbol, quantity, average_price.
    Current prices must come from a separate LTP call.
    """
    stocks = []
    for h in holdings:
        symbol = h.get("trading_symbol", h.get("symbol", ""))
        qty = float(h.get("quantity", 0))
        purchase = float(h.get("average_price", h.get("purchase_price", 0)))

        # LTP lookup — try NSE then BSE key
        ltp = ltp_map.get(f"NSE_{symbol}") or ltp_map.get(f"BSE_{symbol}")
        if ltp is not None:
            current = float(ltp)
        else:
            # Last resort: use avg price (shows 0 gain, clearly wrong rather than misleadingly random)
            current = purchase

        value = round(current * qty, 2)
        gain = round((current - purchase) * qty, 2)
        gain_pct = round(((current - purchase) / purchase) * 100, 2) if purchase else 0.0
        stocks.append({
            "symbol": symbol,
            "quantity": int(qty),
            "purchase_price": purchase,
            "current_price": current,
            "value": value,
            "gain": gain,
            "gain_percent": gain_pct,
        })

    total_value = round(sum(s["value"] for s in stocks), 2)
    total_gain = round(sum(s["gain"] for s in stocks), 2)
    total_invested = round(sum(s["purchase_price"] * s["quantity"] for s in stocks), 2)
    gain_pct = round((total_gain / total_invested) * 100, 2) if total_invested else 0.0
    return {
        "total_value": total_value,
        "total_invested": total_invested,
        "total_gain": total_gain,
        "gain_percent": gain_pct,
        "stocks": stocks,
        "cash_available": round(random.uniform(5000, 50000), 2),
        "updated_at": datetime.now().isoformat(),
    }


@router.get("/")
async def get_portfolio():
    """Portfolio holdings — live from Groww (holdings + LTP), else simulation."""
    client = get_groww_client()
    if client:
        try:
            logger.info(
                "Calling Groww get_holdings",
                extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_holdings"},
            )
            raw = await client.get_holdings()
            if raw:
                # Groww holdings has no price data — fetch LTP separately for all symbols
                symbols = [
                    h.get("trading_symbol", h.get("symbol", ""))
                    for h in raw
                    if h.get("trading_symbol") or h.get("symbol")
                ]
                ltp_map: dict = {}
                if symbols:
                    try:
                        # Fetch NSE prices first
                        logger.info(
                            "Calling Groww get_ltp for holdings",
                            extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_ltp", "symbols": symbols, "exchange": "NSE"},
                        )
                        ltp_data = await client.get_ltp(symbols, exchange="NSE")
                        ltp_map = ltp_data if isinstance(ltp_data, dict) else {}

                        # For symbols with no NSE price, try BSE
                        missing = [s for s in symbols if not ltp_map.get(f"NSE_{s}")]
                        if missing:
                            try:
                                logger.info(
                                    "Calling Groww get_ltp (BSE fallback) for holdings",
                                    extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_ltp", "symbols": missing, "exchange": "BSE"},
                                )
                                bse_data = await client.get_ltp(missing, exchange="BSE")
                                if isinstance(bse_data, dict):
                                    ltp_map.update(bse_data)
                            except Exception:
                                pass
                    except Exception as ltp_err:
                        logger.warning(
                            "LTP fetch for holdings failed",
                            extra={"log_type": "portfolio_event", "event": "ltp_fallback", "error": str(ltp_err)},
                        )

                return {"status": "success", "data": _build_portfolio(raw, ltp_map)}
        except Exception as e:
            logger.warning(
                "Groww holdings fetch failed, using simulation",
                extra={"log_type": "portfolio_event", "event": "holdings_fallback", "error": str(e)},
            )

    # Simulation fallback — use base prices as "current"
    sim_ltp = {f"NSE_{h['symbol']}": h["current_price"] for h in SIM_HOLDINGS}
    return {"status": "success", "data": _build_portfolio(
        [{"trading_symbol": h["symbol"], "quantity": h["quantity"], "average_price": h["average_price"]} for h in SIM_HOLDINGS],
        sim_ltp,
    )}


@router.post("/add")
async def add_to_portfolio(symbol: str, quantity: int, purchase_price: float):
    """Add stock record (informational — actual orders go through /api/orders)."""
    return {
        "status": "success",
        "data": {
            "symbol": symbol.upper(),
            "quantity": quantity,
            "purchase_price": purchase_price,
            "total_cost": round(quantity * purchase_price, 2),
            "status": "recorded",
            "timestamp": datetime.now().isoformat(),
        },
    }


@router.get("/performance")
async def get_performance():
    """Portfolio performance metrics — simulated."""
    return {
        "status": "success",
        "data": {
            "daily_return": round(random.uniform(-5, 5), 2),
            "weekly_return": round(random.uniform(-10, 15), 2),
            "monthly_return": round(random.uniform(-20, 30), 2),
            "yearly_return": round(random.uniform(-30, 50), 2),
            "sharpe_ratio": round(random.uniform(0.5, 2.5), 2),
            "max_drawdown": round(random.uniform(-30, -5), 2),
            "win_rate": round(random.uniform(0.4, 0.8), 2),
            "average_trade_return": round(random.uniform(0.5, 3.0), 2),
            "updated_at": datetime.now().isoformat(),
        },
    }


@router.get("/alerts")
async def get_alerts():
    """Active price/pattern alerts."""
    return {
        "status": "success",
        "count": 3,
        "data": [
            {"id": 1, "symbol": "SBIN",       "alert_type": "price",     "condition": "Price > ₹850",               "enabled": True,  "created_at": datetime.now().isoformat()},
            {"id": 2, "symbol": "INDUSINDBK","alert_type": "pattern",   "condition": "Bullish engulfing detected", "enabled": True,  "created_at": datetime.now().isoformat()},
            {"id": 3, "symbol": "IREDA",     "alert_type": "sentiment", "condition": "Sentiment > 0.7",           "enabled": False, "created_at": datetime.now().isoformat()},
        ],
    }


@router.post("/alerts")
async def create_alert(alert: Alert):
    """Create a new alert."""
    return {
        "status": "success",
        "data": {
            "id": random.randint(1000, 9999),
            "symbol": alert.symbol,
            "alert_type": alert.alert_type,
            "condition": alert.condition,
            "enabled": alert.enabled,
            "created_at": datetime.now().isoformat(),
        },
    }
