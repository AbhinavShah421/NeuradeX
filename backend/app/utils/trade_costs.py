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


# ── Cost viability gate ──────────────────────────────────────────────────────
# Costs were applied at fill time but never allowed to veto an entry: a trade
# whose whole plausible move is smaller than the round trip was taken anyway and
# could not have paid regardless of whether the direction was right.
#
# This is arithmetic, not a prediction. It asks only whether the instrument moves
# enough, on its own recent range, to cover getting in and out. It says nothing
# about which way it will move.

COST_HURDLE_MULT = 3.0      # matches app.research.validation.HURDLE_MULT

# Fraction of one ATR a trade can realistically capture. Measured, not assumed:
# over 12,392 intraday trades the average winner was +0.689% against a median
# ATR of roughly 1% of price, so a full ATR is optimistic and this is set to the
# realised ratio rather than to 1.0.
ATR_CAPTURE = 0.7


def expected_move_pct(price: float, atr: float, capture: float = ATR_CAPTURE) -> float:
    """The move a trade can plausibly capture, as a percent of price."""
    if not price or price <= 0 or not atr or atr <= 0:
        return 0.0
    return 100.0 * (atr * capture) / price


def covers_cost(
    price: float, atr: float, *,
    capture: float = ATR_CAPTURE,
    hurdle_mult: float = COST_HURDLE_MULT,
) -> tuple[bool, float, float]:
    """Can this instrument's own range pay for the round trip?

    Returns (passes, expected_move_pct, hurdle_pct). A zero or missing ATR fails
    closed — an unknown range is not evidence of a tradeable one.
    """
    move = expected_move_pct(price, atr, capture)
    hurdle = hurdle_mult * round_trip_cost_pct()
    return (move >= hurdle and move > 0), move, hurdle
