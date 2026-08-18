"""Raw forward-return labels, as a control on `cf_pnl_pct`.

Why a second label exists
-------------------------
`cf_pnl_pct` is the counterfactual P&L of simulating LIVE_POLICY — an entry, an
ATR-scaled stop, a target, a trail, a time exit. That is the right label for
"would this trade have paid", but it is the wrong label for "does this feature
predict anything", because the exit geometry can manufacture a relationship on
its own.

Concretely: the feature survey found that price extended above its intraday VWAP
predicts a worse simulated long. But a stop placed a fixed number of ATRs below
an extended price is mechanically closer to the recent trading range than the
same stop below a price sitting on its mean. A feature can therefore score by
moving the stop, not by predicting the market.

Raw forward return has no stop, no target and no exit rule — just
(price[t+h] - price[t]) / price[t]. If an effect survives on both labels it is
about the market. If it appears only under the simulated policy it is about the
geometry, and building on it would be building on the exit rules.

Prices come from `session_decisions` itself: decisions are logged per symbol per
minute, so the panel needed to look forward is already in the table. Duplicate
rows for the same minute (concurrent sessions) are averaged.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Sequence

# Horizons in minutes. 60 was the original ceiling only because the live policy's
# stagnation exit fires there — an arbitrary limit, not a property of the market.
# The session runs 09:15-15:30 (375 minutes), so the data supports far longer.
#
# Costs are fixed per round trip while edge grows with holding period, so the
# horizon is the one lever that changes edge/cost without needing a better
# signal. That is what these longer horizons are for.
HORIZONS = (5, 15, 30, 60, 90, 120, 180, 240)

_PANEL_SQL = """
    SELECT created_at::date AS d, symbol, candle_time, AVG(price) AS price
    FROM session_decisions
    WHERE price IS NOT NULL
      AND candle_time IS NOT NULL
      AND created_at::date >= :since
    GROUP BY 1, 2, 3
"""


def _minutes(candle_time: str) -> int | None:
    """'09:16' -> 556. Returns None for anything unparseable."""
    try:
        hh, mm = str(candle_time).split(":")[:2]
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


class PricePanel:
    """Price by (day, symbol, minute-of-day), with forward lookup."""

    def __init__(self) -> None:
        self._p: dict[tuple[date, str], dict[int, float]] = defaultdict(dict)

    def add(self, day: date, symbol: str, candle_time: str, price: float) -> None:
        m = _minutes(candle_time)
        if m is not None:
            self._p[(day, symbol)][m] = price

    def forward_return_pct(
        self, day: date, symbol: str, candle_time: str, horizon_min: int,
        *, tolerance_min: int = 3,
    ) -> float | None:
        """Percent change from `candle_time` to `horizon_min` later.

        The exact target minute may be missing (a gap in decisions, a halted
        symbol), so the nearest minute within `tolerance_min` is accepted. Beyond
        that the observation is dropped rather than stretched — a 60-minute label
        measured over 90 minutes is a different label.
        """
        series = self._p.get((day, symbol))
        m0 = _minutes(candle_time)
        if not series or m0 is None:
            return None
        p0 = series.get(m0)
        if not p0:
            return None

        # Probe the target minute and then outwards, rather than scanning the
        # whole series: this is called once per row per horizon, and a linear
        # scan over a ~900-minute session made the sweep unrunnable.
        target = m0 + horizon_min
        for gap in range(tolerance_min + 1):
            for m in ((target,) if gap == 0 else (target - gap, target + gap)):
                p = series.get(m)
                if p:
                    return 100.0 * (p - p0) / p0
        return None


async def load_panel(since: date) -> PricePanel:
    from sqlalchemy import text
    from app.database.postgres import AsyncSessionLocal

    panel = PricePanel()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(_PANEL_SQL), {"since": since})).fetchall()
    for d, symbol, candle_time, price in rows:
        if price is not None:
            panel.add(d, symbol, candle_time, float(price))
    return panel


def attach_forward_returns(
    rows: Sequence[dict], panel: PricePanel, horizons: Sequence[int] = HORIZONS,
) -> None:
    """Add `fwd_{h}` to each row in place, None where it cannot be computed."""
    for r in rows:
        for h in horizons:
            r[f"fwd_{h}"] = panel.forward_return_pct(
                r["d"], r.get("symbol") or "", r.get("candle_time") or "", h
            )
