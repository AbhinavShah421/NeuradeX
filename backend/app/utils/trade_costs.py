"""Realistic trade-cost model for NSE intraday equity, so simulated P&L is
net-of-cost (what you'd actually keep), not gross.

Two parts:
  • Slippage / spread — applied to the FILL price: a market BUY fills a touch
    above the close, a SELL a touch below. (TRADE_SLIPPAGE_BPS, per side)
  • Charges — brokerage, exchange transaction, GST, STT and stamp duty,
    deducted from the trade's P&L.

WHY BROKERAGE IS NOT A FLAT BASIS-POINT FIGURE
----------------------------------------------
Until 2026-09-10 this module charged a flat `TRADE_FEE_BPS = 3.0` per side and
reported a 0.125% round trip. Brokers do not price that way. Groww charges the
LOWER of Rs20 or 0.05% per order, so the rate depends on order value:

    Rs 5,000 order  → 0.05% = Rs2.50   → 5.0 bps   (the percentage binds)
    Rs 40,000 order → 0.05% = Rs20     → 5.0 bps   (the crossover)
    Rs200,000 order → capped at Rs20   → 1.0 bps   (the cap binds)

The median position this system actually trades is ~Rs5,000, which pays 5 bps
per side, not 3. Add the stamp duty this model omitted entirely and the real
round trip at that size is ~0.193%, not 0.125% — every backtest, expectancy
figure and "this strategy works" conclusion computed against the old number was
flattered by roughly 1.5x.

Measured 2026-09-10: gross intraday edge is +0.030%/trade, so the difference
between 0.125% and 0.193% is not a rounding detail — it is several times the
entire edge.

`charges()` already receives the turnover, so it computes brokerage properly
rather than assuming. `round_trip_cost_pct()` now takes an optional turnover
because the answer genuinely depends on it; callers that omit it get the size
this system actually trades, not an optimistic one.
"""
from __future__ import annotations
from app.config import settings


def _cfg(name: str, default: float) -> float:
    return float(getattr(settings, name, default))


def _slip() -> float:
    return _cfg("TRADE_SLIPPAGE_BPS", 2.0) / 10000.0


def buy_fill(price: float) -> float:
    """A market buy fills slightly above the quoted price."""
    return round(price * (1 + _slip()), 4)


def sell_fill(price: float) -> float:
    """A market sell fills slightly below the quoted price."""
    return round(price * (1 - _slip()), 4)


def brokerage(turnover: float) -> float:
    """Brokerage in rupees for ONE leg — the lower of a flat cap and a percentage.

    This is the whole reason the cost model needed rewriting: it is not linear in
    turnover, so it cannot be expressed as a basis-point constant.
    """
    if not turnover or turnover <= 0:
        return 0.0
    pct = turnover * _cfg("TRADE_BROKERAGE_PCT", 0.05) / 100.0
    return min(_cfg("TRADE_BROKERAGE_CAP", 20.0), pct)


def charges(entry_fill: float, exit_fill: float, qty: int) -> float:
    """Round-trip statutory + broker charges in rupees.

    Slippage is NOT included — it is already inside the fill prices.
    """
    buy_turn = entry_fill * qty
    sell_turn = exit_fill * qty
    if buy_turn <= 0 and sell_turn <= 0:
        return 0.0

    brok = brokerage(buy_turn) + brokerage(sell_turn)
    exch = (buy_turn + sell_turn) * _cfg("TRADE_EXCHANGE_BPS", 0.297) / 10000.0
    # GST applies to the broker's own charges, not to the statutory taxes.
    gst = (brok + exch) * _cfg("TRADE_GST_PCT", 18.0) / 100.0
    stt = sell_turn * _cfg("TRADE_STT_BPS", 2.5) / 10000.0     # sell leg only
    stamp = buy_turn * _cfg("TRADE_STAMP_BPS", 0.3) / 10000.0  # buy leg only
    return round(brok + exch + gst + stt + stamp, 2)


def round_trip_cost_pct(turnover: float | None = None) -> float:
    """Total round-trip cost as a % of turnover, INCLUDING slippage.

    `turnover` is the value of one leg. Omit it and you get the cost at the
    position size this system actually trades (TRADE_TYPICAL_TURNOVER), which is
    the honest default for reporting — an optimistic one is how a strategy gets
    declared viable on paper and loses money live.
    """
    turn = turnover if (turnover and turnover > 0) else _cfg("TRADE_TYPICAL_TURNOVER", 5000.0)
    # Same price both legs: this is a cost rate, not a P&L.
    fees = charges(turn, turn, 1)
    slip = 2 * _cfg("TRADE_SLIPPAGE_BPS", 2.0) / 100.0
    return round(fees / turn * 100.0 + slip, 4)


def cost_floor_pct() -> float:
    """The cheapest round trip physically available: zero brokerage, limit orders.

    Only the statutory charges — STT, exchange, stamp and the GST on the exchange
    fee. Nothing a broker or an order type can remove. Measured against the
    +0.030% gross intraday edge, this floor is what says the intraday horizon
    cannot be rescued by cost engineering.
    """
    exch = 2 * _cfg("TRADE_EXCHANGE_BPS", 0.297)
    gst = exch * _cfg("TRADE_GST_PCT", 18.0) / 100.0
    stt = _cfg("TRADE_STT_BPS", 2.5)
    stamp = _cfg("TRADE_STAMP_BPS", 0.3)
    return round((exch + gst + stt + stamp) / 100.0, 4)


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
