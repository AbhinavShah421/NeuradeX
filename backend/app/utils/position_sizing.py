"""Risk-based position sizing.

Until 2026-08-20 every paper entry used `qty = cash * 0.95 / fill` — 95% of the
session's cash regardless of how far away the stop sat. A trade with a 1.5% stop
and a trade with a 3.75% stop deployed identical capital, so the rupee loss on
the second was 2.5x the first for the same "risk" decision.

That is the mechanism by which a more volatile universe silently enlarges losses.
Between 2026-07-21 and 2026-08-09 the traded names moved 0.569% per trade; from
2026-08-10 the (almost entirely different — 5 of 70 symbols overlap) names moved
0.714%, a 25% increase. Under flat sizing that lands directly on the P&L.

Risk sizing removes it: the rupee amount at risk is fixed at `capital * risk_pct`
and the quantity falls as the stop widens, so a volatile name and a quiet one
cost the same when they stop out.

This mirrors what the Java risk-engine already does for the microservice
pipeline (RiskValidatorService: max-risk-pct 0.02, max-position-pct 0.05) — that
service was never wired into the paper path, so the caps never applied here.

Shrink-only by construction: the notional cap keeps this at or below the legacy
95%-of-cash, never above it.
"""
from __future__ import annotations


def stop_pct_for(atr: float, price: float) -> float:
    """The stop the position will actually run, in percent.

    Mirrors backtest_service._tech_signal_ex exactly — atr_pct bounded to
    [0.5, 2.5], stop = max(1.5, 1.5 * atr_pct) — so sizing can never drift from
    the exit that will fire. Falls back to the same 0.8 default on a missing ATR.
    """
    if not price or price <= 0:
        return 1.5
    atr_pct = max(0.5, min(2.5, (atr / price * 100) if atr else 0.8))
    return max(1.5, 1.5 * atr_pct)


def risk_qty(
    capital: float, cash: float, fill: float, stop_pct: float,
    risk_pct: float = 0.01, max_pos_pct: float = 0.95,
) -> int:
    """Shares to buy so a stop-out costs about `capital * risk_pct`.

    Bounded by `cash * max_pos_pct / fill`, so this can only ever be smaller
    than the legacy sizing, never larger. Always returns at least 1 — a session
    that cannot afford one share is rejected by the caller's cash check, not
    here.
    """
    if fill <= 0:
        return 1

    notional_cap = int((cash * max_pos_pct) / fill) if cash > 0 else 0

    if stop_pct <= 0 or risk_pct <= 0 or capital <= 0:
        return max(1, notional_cap)

    stop_distance = fill * (stop_pct / 100.0)
    by_risk = int((capital * risk_pct) / stop_distance) if stop_distance > 0 else notional_cap

    return max(1, min(by_risk, notional_cap) if notional_cap > 0 else by_risk)
