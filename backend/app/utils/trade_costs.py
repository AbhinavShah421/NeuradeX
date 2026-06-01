"""Realistic trade-cost model for NSE intraday equity, so simulated P&L is
net-of-cost (what you'd actually keep), not gross.

Two parts, both configurable (basis points):
  • Slippage / spread — applied to the FILL price: a market BUY fills a touch
    above the close, a SELL a touch below. (TRADE_SLIPPAGE_BPS, per side)
  • Charges — brokerage + exchange + GST (TRADE_FEE_BPS, per side) plus STT on
    the sell turnover (TRADE_STT_BPS), deducted from the trade's P&L.

Defaults model a discount broker on a liquid stock (~0.125% round trip):
slippage 2bps×2 + fee 3bps×2 + STT 2.5bps ≈ 12.5 bps.
"""
from __future__ import annotations
from app.config import settings


def _slip() -> float:
    return float(getattr(settings, "TRADE_SLIPPAGE_BPS", 2.0)) / 10000.0


def buy_fill(price: float) -> float:
    """A market buy fills slightly above the quoted price."""
    return round(price * (1 + _slip()), 4)


def sell_fill(price: float) -> float:
    """A market sell fills slightly below the quoted price."""
    return round(price * (1 - _slip()), 4)


def charges(entry_fill: float, exit_fill: float, qty: int) -> float:
    """Round-trip brokerage + exchange + GST + STT in rupees (slippage is already
    in the fill prices)."""
    buy_turn = entry_fill * qty
    sell_turn = exit_fill * qty
    fee = (buy_turn + sell_turn) * float(getattr(settings, "TRADE_FEE_BPS", 3.0)) / 10000.0
    stt = sell_turn * float(getattr(settings, "TRADE_STT_BPS", 2.5)) / 10000.0
    return round(fee + stt, 2)


def round_trip_cost_pct() -> float:
    """Approximate total round-trip cost as a % of turnover (for reporting)."""
    slip = 2 * float(getattr(settings, "TRADE_SLIPPAGE_BPS", 2.0))
    fee = 2 * float(getattr(settings, "TRADE_FEE_BPS", 3.0))
    stt = float(getattr(settings, "TRADE_STT_BPS", 2.5))
    return round((slip + fee + stt) / 100.0, 4)
