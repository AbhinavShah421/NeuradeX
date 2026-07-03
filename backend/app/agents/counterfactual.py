"""Counterfactual learning — full-information training from the decisions we DIDN'T take.

The sessions make ~400 decisions/day per symbol but execute almost none of them,
so the learning loop starves: RL gets bandit feedback (rewards only for executed
BUYs), the action-accuracy rates accrue a handful of samples a day, and the
pattern-memory bank stays dominated by stale backtest sims.

Meanwhile the 1-second tick store records exactly what the market did after every
one of those skipped decisions. This module closes the loop:

  1. LABEL   — for every non-executed decision persisted in `session_decisions`,
               simulate the long trade the system declined (entry next bar open
               with slippage, exit per the same ATR stop/target/trail rules the
               live sessions use, real round-trip costs) against the recorded
               1-minute bars, and store the counterfactual P&L on the row.
  2. TRAIN   — feed the labels to the consumers, each behind its own guard rail:
        RL      : per-state mean reward, Q[BUY] and Q[HOLD] updated at
                  _CF_LR_SCALE × the real-trade learning rate (simulated labels
                  must never outweigh real outcomes).
        rates   : merged into the action-accuracy sync at _CF_RATE_WEIGHT per
                  sample (learning.py reads the labeled rows directly).
        memory  : phantom BUY cases ONLY for "near-miss" decisions (2+ agents
                  voted BUY but a gate vetoed), capped per day, source='CF'.

Runs in the runner/full role off-hours (same cadence family as volume
enrichment); every step is idempotent per (day, consumer).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_CF_LR_SCALE          = 0.3     # RL learning-rate scale for simulated labels
_MEM_PHANTOM_DAILY_CAP = 50     # max phantom memory cases promoted per day
_MEM_PHANTOM_MIN_BUYS  = 2      # "near-miss" = at least this many agents voted BUY
_MIN_DAY_TICKS         = 1000   # skip days with thin tick coverage (bad labels)
_SWEEP_EVERY           = 900.0  # seconds between off-hours sweeps
_LABEL_BATCH_DAYS      = 3      # look back at most this many trading days

_SQUAREOFF_MIN = 14 * 60 + 45   # 14:45 IST forced square-off (same as _tech_signal)

_RL_TRAINED_KEY  = "counterfactual:rl_trained:"     # + date → dedupe flag
_MEM_TRAINED_KEY = "counterfactual:mem_trained:"    # + date → dedupe flag

_last_sweep = 0.0


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = [
    # Label columns on the existing decisions table.
    "ALTER TABLE session_decisions ADD COLUMN IF NOT EXISTS cf_pnl_pct DOUBLE PRECISION",
    "ALTER TABLE session_decisions ADD COLUMN IF NOT EXISTS cf_labeled_at TIMESTAMPTZ",
    # One-time dedupe of the per-advance-tick duplicate rows written before the
    # persist path was upserted — keep the newest row per (session, candle).
    """DELETE FROM session_decisions a USING session_decisions b
       WHERE a.session_id = b.session_id AND a.candle_time = b.candle_time
         AND a.id < b.id""",
    # Required by the persist path's ON CONFLICT (session_id, candle_time).
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_session_decisions_candle
       ON session_decisions (session_id, candle_time)""",
    # Cheap pending scans.
    """CREATE INDEX IF NOT EXISTS idx_session_decisions_cf_pending
       ON session_decisions (created_at) WHERE cf_labeled_at IS NULL""",
]


async def init_schema() -> None:
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))
        logger.info("counterfactual schema ready",
                    extra={"log_type": "ai_engine", "event": "cf_schema_init"})
    except Exception as exc:
        logger.warning("counterfactual schema init failed: %s", exc)


# ── Trade simulation (mirrors the live LONG exit policy) ─────────────────────

def _simulate_long(bars: list[dict], entry_idx: int, max_hold_minutes: int = 30) -> Optional[float]:
    """Simulate the long the system declined: enter at bars[entry_idx]'s open
    (buy-side slippage), exit per the same rules `_tech_signal` applies to a live
    LONG (ATR-scaled stop/target, profit-lock trail, fast cut, square-off), or at
    the hold cap. Returns net pnl% including round-trip costs, or None if the
    day's data can't support a simulation (entry at/after the end of bars)."""
    from app.services.backtest_service import _intraday_indicators, _tech_signal
    from app.utils.trade_costs import buy_fill, sell_fill, charges

    if entry_idx >= len(bars):
        return None
    entry = buy_fill(bars[entry_idx]["open"])
    if entry <= 0:
        return None
    qty = max(1, int(50_000 / entry))          # session-default sizing; pnl% is size-invariant

    exit_price: Optional[float] = None
    last_i = entry_idx
    for i in range(entry_idx, len(bars)):
        last_i = i
        c = bars[i]
        held_min = i - entry_idx
        try:
            h, m = int(c["time"].split(":")[0]), int(c["time"].split(":")[1])
        except (KeyError, ValueError, IndexError):
            h, m = 0, 0
        if held_min >= max_hold_minutes or (h * 60 + m) >= _SQUAREOFF_MIN:
            exit_price = sell_fill(c["close"])
            break
        ind = _intraday_indicators(bars, i)
        if _tech_signal(ind, "LONG", c, entry) == -1:
            exit_price = sell_fill(c["close"])
            break
    if exit_price is None:                      # ran off the end of the day's bars
        exit_price = sell_fill(bars[last_i]["close"])

    fees = charges(entry, exit_price, qty)
    pnl  = qty * (exit_price - entry) - fees
    return round(pnl / (qty * entry) * 100, 3)


def _bar_index_for_time(bars: list[dict], hhmm: str) -> Optional[int]:
    """Index of the bar whose time matches HH:MM (decisions are per 1-min candle)."""
    for i, b in enumerate(bars):
        if b.get("time") == hhmm:
            return i
    return None


# ── Labeling ──────────────────────────────────────────────────────────────────

async def label_pending(max_days: int = _LABEL_BATCH_DAYS) -> dict:
    """Label every unlabeled, completed-day decision with its counterfactual pnl%.
    Groups by (symbol, day) so each day's bars are read once. Days with thin tick
    coverage are marked labeled-with-NULL so they aren't rescanned forever."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.data.candle_store import read_bars, day_coverage

    today = datetime.now(IST).date().isoformat()
    since = (datetime.now(IST) - timedelta(days=max_days + 4)).date().isoformat()

    async with engine.begin() as conn:
        # The decision's market day: for backtest/replay sessions created_at is
        # when the REPLAY ran, not the day being traded — session_metadata.date
        # is authoritative. Paper/live sessions fall back to created_at's IST date.
        rows = (await conn.execute(text("""
            SELECT d.id, d.session_id, d.symbol, d.candle_time,
                   COALESCE(sm.date,
                            (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) AS day
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_labeled_at IS NULL
              AND COALESCE(sm.date,
                           (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) < :today
              AND (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text >= :since
            ORDER BY d.created_at
            LIMIT 20000
        """), {"today": today, "since": since})).fetchall()

    if not rows:
        return {"labeled": 0, "skipped": 0, "days": 0}

    by_day: dict[tuple[str, str], list] = {}
    for r in rows:
        by_day.setdefault((r[2].upper(), r[4]), []).append(r)

    labeled = skipped = 0
    for (symbol, day), day_rows in by_day.items():
        cov = await asyncio.to_thread(day_coverage, symbol, day)
        bars = (await asyncio.to_thread(read_bars, symbol, day, 60)) \
            if cov.get("ticks", 0) >= _MIN_DAY_TICKS else []

        updates = []
        for r in day_rows:
            cf = None
            if bars:
                idx = _bar_index_for_time(bars, r[3])
                if idx is not None and idx + 1 < len(bars):
                    # Enter at the NEXT bar's open — the decision is made on the
                    # closed candle, exactly like the live entry path.
                    cf = _simulate_long(bars, idx + 1)
            updates.append({"id": r[0], "cf": cf})
            if cf is None:
                skipped += 1
            else:
                labeled += 1

        from sqlalchemy import text as _t
        async with engine.begin() as conn:
            for u in updates:
                await conn.execute(_t("""
                    UPDATE session_decisions
                    SET cf_pnl_pct = :cf, cf_labeled_at = NOW()
                    WHERE id = :id
                """), u)

    logger.info("counterfactual labeling: %d labeled, %d unlabelable, %d symbol-days",
                labeled, skipped, len(by_day),
                extra={"log_type": "ai_engine", "event": "cf_labeled",
                       "labeled": labeled, "skipped": skipped, "days": len(by_day)})
    return {"labeled": labeled, "skipped": skipped, "days": len(by_day)}


# ── Consumers ─────────────────────────────────────────────────────────────────

async def train_rl_from_labels(day: str) -> int:
    """Full-information Q-updates from one day's labels: per state visited that
    day, apply ONE update per action from the mean counterfactual reward — Q[BUY]
    gets the reward of the entry the system declined; Q[HOLD] gets the abstention
    mirror (credit for skipping losers, small miss-penalty for skipping winners;
    same convention as learning.py's weight updates). Aggregating per state keeps
    the daily CF influence bounded to ≤ 2×108 updates at _CF_LR_SCALE × LR.
    Idempotent per day via a Redis flag."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.redis_client import get_redis
    from app.data.candle_store import read_bars
    from app.agents import get_rl_agent
    from app.agents.learning import LearningSystem
    from app.agents.rl_agent import ACTIONS, LR

    r = get_redis()
    flag = _RL_TRAINED_KEY + day
    if await r.get(flag):
        return 0

    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT d.symbol, d.candle_time, d.cf_pnl_pct
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_pnl_pct IS NOT NULL AND d.executed = FALSE
              AND COALESCE(sm.date,
                           (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) = :day
        """), {"day": day})).fetchall()
    if not rows:
        return 0

    rl = get_rl_agent()
    bars_cache: dict[str, list[dict]] = {}
    state_rewards: dict[int, list[float]] = {}
    for sym, hhmm, cf in rows:
        sym = sym.upper()
        if sym not in bars_cache:
            bars_cache[sym] = await asyncio.to_thread(read_bars, sym, day, 60)
        bars = bars_cache[sym]
        idx = _bar_index_for_time(bars, hhmm)
        if idx is None or idx < 26:            # extract_state needs ≥26 candles
            continue
        state = rl.extract_state(bars[: idx + 1])
        state_rewards.setdefault(state, []).append(LearningSystem._reward(float(cf)))

    buy_idx, hold_idx = ACTIONS.index("BUY"), ACTIONS.index("HOLD")
    cf_lr = LR * _CF_LR_SCALE
    updates = 0
    for state, rewards in state_rewards.items():
        mean_r = sum(rewards) / len(rewards)
        await rl.update(state, buy_idx, mean_r, state, lr=cf_lr)
        # Abstention mirror: skipping a loser is half-credit, skipping a winner
        # is a small miss-penalty — matches the weight-update convention.
        hold_r = abs(mean_r) * 0.5 if mean_r < 0 else -mean_r * 0.15
        await rl.update(state, hold_idx, hold_r, state, lr=cf_lr)
        updates += 2

    await r.set(flag, "1", ex=86400 * 7)
    logger.info("counterfactual RL training: %d states, %d updates (%s)",
                len(state_rewards), updates, day,
                extra={"log_type": "ai_engine", "event": "cf_rl_trained",
                       "day": day, "states": len(state_rewards)})
    return updates


async def promote_memory_phantoms(day: str) -> int:
    """Promote near-miss decisions (≥ _MEM_PHANTOM_MIN_BUYS agents voted BUY but
    no trade fired) into the pattern-memory bank as phantom BUY cases, labeled
    with the counterfactual result. Capped per day and tagged source='CF' so real
    cases always dominate a similarity neighbourhood. Idempotent per day."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.redis_client import get_redis
    from app.data.candle_store import read_bars
    from app.agents import get_memory
    from app.agents.fingerprint import build_fingerprint, classify_regime

    r = get_redis()
    flag = _MEM_TRAINED_KEY + day
    if await r.get(flag):
        return 0

    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT d.symbol, d.candle_time, d.cf_pnl_pct, d.agents
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_pnl_pct IS NOT NULL AND d.executed = FALSE
              AND COALESCE(sm.date,
                           (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) = :day
            ORDER BY ABS(d.cf_pnl_pct) DESC
        """), {"day": day})).fetchall()

    memory = get_memory()
    bars_cache: dict[str, list[dict]] = {}
    promoted = 0
    for sym, hhmm, cf, agents_raw in rows:
        if promoted >= _MEM_PHANTOM_DAILY_CAP:
            break
        try:
            agents = agents_raw if isinstance(agents_raw, list) else json.loads(agents_raw or "[]")
        except Exception:
            continue
        buy_votes = sum(1 for a in agents if (a or {}).get("action") == "BUY")
        if buy_votes < _MEM_PHANTOM_MIN_BUYS:
            continue
        sym = sym.upper()
        if sym not in bars_cache:
            bars_cache[sym] = await asyncio.to_thread(read_bars, sym, day, 60)
        bars = bars_cache[sym]
        idx = _bar_index_for_time(bars, hhmm)
        if idx is None:
            continue
        window = bars[: idx + 1]
        fp = build_fingerprint(window)
        if not fp:
            continue
        px = float(bars[idx]["close"])
        await memory.add_case(
            symbol=sym, fingerprint=fp, action="BUY", pnl_pct=float(cf),
            entry_price=px, exit_price=round(px * (1 + float(cf) / 100), 2),
            regime=classify_regime(window), source="CF",
        )
        promoted += 1

    await r.set(flag, "1", ex=86400 * 7)
    if promoted:
        logger.info("counterfactual memory: %d phantom near-miss cases promoted (%s)",
                    promoted, day,
                    extra={"log_type": "ai_engine", "event": "cf_memory_promoted",
                           "day": day, "promoted": promoted})
    return promoted


# ── Background loop (runner/full role) ────────────────────────────────────────

def _market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 14) <= mins <= (15 * 60 + 31)


def _last_completed_trading_day(now: datetime) -> str:
    d = now.date()
    mins = now.hour * 60 + now.minute
    if d.weekday() < 5 and mins >= (15 * 60 + 35):
        return d.isoformat()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


async def counterfactual_loop() -> None:
    """Off-hours: label pending decisions against the tick store, then run the
    RL and memory consumers for the last completed trading day (each is
    idempotent per day, so re-running is free). The action-rate merge needs no
    step here — learning.py reads the labeled rows on its normal sync."""
    global _last_sweep
    await asyncio.sleep(40)
    await init_schema()
    logger.info("counterfactual loop started",
                extra={"log_type": "app_lifecycle", "event": "cf_loop_started"})
    while True:
        try:
            if not _market_hours() and (time.time() - _last_sweep) >= _SWEEP_EVERY:
                _last_sweep = time.time()
                await label_pending()
                day = _last_completed_trading_day(datetime.now(IST))
                await train_rl_from_labels(day)
                await promote_memory_phantoms(day)
        except Exception as exc:
            logger.warning("counterfactual sweep failed: %s", exc)
        await asyncio.sleep(60)
