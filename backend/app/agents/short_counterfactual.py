"""Short-side counterfactual labels. Research only: NeuradeX does not short.

Gap found 2026-11-25 comparing NeuradeX with how profitable intraday traders
work: the system is long-only. On a down day it can sit out or buy the fall,
never sell the weakness. Both of that day's losses (MFSL, a gap-up fading all
day; MEESHO, a failed breakout at the prior-day high) were textbook short
setups for a human trader.

Before anyone builds short execution, this answers the only question that
matters: had each recorded decision been a SHORT, under the live exit rules
mirrored and real costs, would it have cleared costs? The label goes in
session_decisions.cf_short_pnl_pct next to the long label cf_pnl_pct, from the
same bars, so the two compare decision for decision.

The mirror of counterfactual._simulate_policy under LIVE_POLICY:
  entry       sell_fill(next bar open); exit buy_fill(close)
  stop/target same -max(1.5, ...) / +max(2.5, ...) on the short's gain
  grace       first 10 min: only the 2x disaster stop
  hold review 60 min; a profitable short in an intact DOWNtrend
              (price <= sma5 <= sma20) keeps riding (the long's cap_trend_extend)
  lock        gain >= 0.8% and price back ABOVE sma5 with mom5 > 0
  rsi exit    rsi < 25 and mom5 > 0 (the oversold bounce, mirrored)
  square-off  14:45
  charges     buy leg = the exit, sell leg = the entry, so STT falls on the
              entry, as on a real intraday short
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SQUAREOFF_MIN = 14 * 60 + 45
_DDL = "ALTER TABLE session_decisions ADD COLUMN IF NOT EXISTS cf_short_pnl_pct DOUBLE PRECISION"


def simulate_short(bars: list[dict], inds: list[dict], entry_idx: int,
                   policy: Optional[dict] = None) -> Optional[float]:
    """Net pnl% of a short entered at bars[entry_idx].open, or None."""
    from app.agents.counterfactual import LIVE_POLICY
    from app.utils.trade_costs import buy_fill, sell_fill, charges

    policy = policy or LIVE_POLICY
    if entry_idx >= len(bars):
        return None
    entry = sell_fill(bars[entry_idx]["open"])
    if entry <= 0:
        return None
    qty = max(1, int(50_000 / entry))

    hold_cap = int(policy.get("hold_cap", 30))
    grace_min = int(policy.get("grace_min", 0))
    trail = policy.get("trail")
    lwm = entry                                    # low-water mark (best for a short)

    exit_price: Optional[float] = None
    last_i = entry_idx
    for i in range(entry_idx, len(bars)):
        last_i = i
        c = bars[i]
        price = c["close"]
        lwm = min(lwm, price)
        held = i - entry_idx
        try:
            h, m = int(c["time"].split(":")[0]), int(c["time"].split(":")[1])
        except (KeyError, ValueError, IndexError):
            h, m = 0, 0

        ind = inds[i]
        gain = (entry - price) / entry * 100
        raw_atr = (ind.get("atr", 0.0) / price * 100) if price else 0.8
        atr_pct = max(policy.get("atr_floor", 0.5),
                      min(policy.get("atr_cap", 2.5), raw_atr * policy.get("atr_scale", 1.0)))
        stop = -max(policy["stop_floor"], policy["stop_atr_mult"] * atr_pct)
        take = max(policy["take_floor"], policy["take_atr_mult"] * atr_pct)
        sma5, sma20, mom5 = ind.get("sma5", 0.0), ind.get("sma20", 0.0), ind.get("mom5", 0.0)

        capped = held >= hold_cap
        if (capped and policy.get("cap_trend_extend")
                and gain > 0 and price <= sma5 <= sma20):
            capped = False
        if capped or (h * 60 + m) >= _SQUAREOFF_MIN:
            exit_price = buy_fill(price)
            break

        in_grace = held < grace_min
        if in_grace:
            if gain <= 2 * stop:
                exit_price = buy_fill(price)
                break
            continue                               # LIVE_POLICY has no grace_upside
        if gain <= stop or gain >= take:
            exit_price = buy_fill(price)
            break
        if trail == "sma5":
            if (gain >= policy["lock_gain"] and price > sma5
                    and (mom5 > 0 or not policy.get("lock_confirm_mom"))):
                exit_price = buy_fill(price)
                break
        elif trail == "hwm_atr":
            if gain >= policy["lock_gain"] and price > lwm * (1 + atr_pct / 100):
                exit_price = buy_fill(price)
                break
        if policy.get("fast_cut") and gain < 0.5:
            if (sma5 > sma20 and mom5 > 0.15) or mom5 > 0.30:
                exit_price = buy_fill(price)
                break
        if policy.get("rsi_exit") and ind.get("rsi", 50.0) < 25 and mom5 > 0:
            exit_price = buy_fill(price)
            break
    if exit_price is None:
        exit_price = buy_fill(bars[last_i]["close"])

    fees = charges(exit_price, entry, qty)         # buy leg = exit, sell leg = entry
    pnl = qty * (entry - exit_price) - fees
    return round(pnl / (qty * entry) * 100, 3)


async def init_schema() -> None:
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        await conn.execute(text(_DDL))


async def backfill(days: int = 120, max_symbol_days: int = 100_000) -> dict:
    """Short-label every already long-labelled decision that has no short label
    yet, one symbol-day at a time, from the same bars the long label used (the
    tick store, else provider bars). Safe to re-run; it resumes where it stopped."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.data.candle_store import read_bars, day_coverage
    from app.agents.counterfactual import (_MIN_DAY_TICKS, _bar_index_for_time,
                                           _day_indicators, _provider_bars)

    await init_schema()
    # One read of every pending decision, grouped here: a per-symbol-day query
    # was a full scan of session_decisions each time (~38 rows/s overall).
    async with engine.begin() as conn:
        pending = (await conn.execute(text("""
            SELECT d.id, upper(d.symbol),
                   COALESCE(sm.date, (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) AS day,
                   d.candle_time
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_pnl_pct IS NOT NULL AND d.cf_short_pnl_pct IS NULL
              AND d.created_at > now() - make_interval(days => :days)
        """), {"days": days})).fetchall()
    by_group: dict[tuple[str, str], list] = {}
    for rid, sym, day, hhmm in pending:
        by_group.setdefault((sym, day), []).append((rid, hhmm))
    groups = sorted(by_group, key=lambda g: g[1], reverse=True)[:max_symbol_days]

    labeled = no_bars = 0
    for n, (symbol, day) in enumerate(groups):
        cov = await asyncio.to_thread(day_coverage, symbol, day)
        bars = (await asyncio.to_thread(read_bars, symbol, day, 60))             if cov.get("ticks", 0) >= _MIN_DAY_TICKS else []
        if not bars:
            bars = await _provider_bars(symbol, day)
        if not bars:
            no_bars += 1
            continue
        inds = await asyncio.to_thread(_day_indicators, bars)
        by_time: dict[str, Optional[float]] = {}
        ups = []
        for rid, hhmm in by_group[(symbol, day)]:
            if hhmm not in by_time:                # replays repeat the same bar
                idx = _bar_index_for_time(bars, hhmm)
                by_time[hhmm] = (simulate_short(bars, inds, idx + 1)
                                 if idx is not None and idx + 1 < len(bars) else None)
            if by_time[hhmm] is not None:
                ups.append({"cf": by_time[hhmm], "id": rid})
        if ups:                                    # one executemany per symbol-day
            async with engine.begin() as conn:
                await conn.execute(text(
                    "UPDATE session_decisions SET cf_short_pnl_pct = :cf WHERE id = :id"), ups)
            labeled += len(ups)
        if n % 50 == 0:
            logger.info("short cf backfill: %d/%d symbol-days, %d labelled, %d without bars",
                        n, len(groups), labeled, no_bars)
        await asyncio.sleep(0)
    out = {"symbol_days": len(groups), "labeled": labeled, "no_bars": no_bars}
    logger.info("short cf backfill done: %s", out)
    return out
