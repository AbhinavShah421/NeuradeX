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


# ── Trade simulation (policy-parameterised LONG exit engine) ─────────────────
# The baseline policy replicates `_tech_signal`'s live LONG rules exactly; the
# other variants are the exit-policy A/B candidates evaluated nightly on the
# same entries (see run_exit_ab). Forensic motivation: 7.6k trades exited inside
# 30 minutes won 15% (avg -0.23 to -0.32%), hold-cap exits won 30%, and trades
# that survived past 31 minutes won 48.9% (+0.16%) — the tight stop / fast-cut /
# SMA5-trail cluster crystallises 1-minute noise as losses.

# The OLD tight exit policy (pre-2026-07-03 _tech_signal rules). Kept as the
# A/B's "baseline" variant so day-over-day comparisons stay continuous.
BASELINE_POLICY: dict = {
    "stop_atr_mult": 0.9,  "stop_floor": 1.0,     # stop  = -max(floor, mult×ATR%)
    "take_atr_mult": 1.8,  "take_floor": 2.5,     # take  = +max(floor, mult×ATR%)
    "lock_gain": 1.2,      "trail": "sma5",        # profit-lock trail type
    "fast_cut": True,       "rsi_exit": True,      # momentum cut / overbought exit
    "grace_min": 0,                                # stop active immediately
    "hold_cap": 30,                                # minutes
}

# The exit policy the live sessions actually run (_tech_signal LONG rules) —
# CF labels must track this. Updated 2026-07-03 to the A/B winner "wide_hold60"
# (wider ATR stop, 10-min grace, no fast momentum cuts, 60-min runway): +12pts
# win-rate on ~13k identical entries across four independent populations.
LIVE_POLICY: dict = {
    **BASELINE_POLICY,
    "stop_atr_mult": 1.5, "stop_floor": 1.5,
    "fast_cut": False, "grace_min": 10, "hold_cap": 60,
    # lock_gain 1.2 → 0.8 adopted live 2026-07-09 (the wide_hold60_lock08
    # variant won the win-rate A/B on both days since introduction). CF labels
    # must keep tracking the live exit rules (_tech_signal in backtest_service).
    "lock_gain": 0.8,
    # "Let winners run" pair adopted live 2026-07-10 after the post-exit audit
    # (Jul 8-10, 211 exits): flat 60-min cap exits alone left +0.51% avg /
    # 56.9% total on the table with the trend still intact, and the SMA5 lock
    # was cashing rising stocks out at ~+0.9%. hold_cap is now a REVIEW point:
    # trend-intact winners ride until the trend breaks or square-off, and the
    # lock needs mom5 < 0 confirmation. A user-set session cap stays a hard
    # flat cap (manual mode). Pre-change policy stays in the A/B as
    # wide_hold60_lock08.
    "cap_trend_extend": True,
    "lock_confirm_mom": True,
}

EXIT_VARIANTS: dict[str, dict] = {
    "baseline":    BASELINE_POLICY,
    # Wider stop + entry grace, no noise-cuts — "let it breathe" at the same cap.
    "wide_stop":   {**BASELINE_POLICY, "stop_atr_mult": 1.5, "stop_floor": 1.5,
                    "fast_cut": False, "grace_min": 10},
    # Same exits, double the runway.
    "hold60":      {**BASELINE_POLICY, "hold_cap": 60},
    # Both: wide stop + 60-min runway.
    "wide_hold60": {**BASELINE_POLICY, "stop_atr_mult": 1.5, "stop_floor": 1.5,
                    "fast_cut": False, "grace_min": 10, "hold_cap": 60},
    # High-water-mark ATR trail instead of the SMA5 touch, no cuts, 60 min.
    "hwm_trail60": {**BASELINE_POLICY, "trail": "hwm_atr", "fast_cut": False,
                    "rsi_exit": False, "grace_min": 10, "hold_cap": 60},
    # Isolate the fast-cut/rsi effect: baseline stops, no cuts, same 30-min cap.
    "no_cut30":    {**BASELINE_POLICY, "fast_cut": False, "rsi_exit": False},
    # Live policy with an earlier profit lock (0.8% vs 1.2%). Motivated by the
    # 2026-07-07 AVANTEL replay: +1.01% MFE decayed to flat because the lock
    # never armed at 1.2%. Candidate — adopt only if the A/B proves it.
    "wide_hold60_lock08": {**BASELINE_POLICY, "stop_atr_mult": 1.5, "stop_floor": 1.5,
                           "fast_cut": False, "grace_min": 10, "hold_cap": 60,
                           "lock_gain": 0.8},
    # Live since 2026-07-10: lock08 + auto hold review (winners in an intact
    # uptrend ride past the 60m review point until the trend breaks or
    # square-off) + the SMA5 lock requiring mom5 < 0. Motivated by the
    # Jul 8-10 post-exit audit — see LIVE_POLICY.
    "wide_hold60_lock08_run": {**BASELINE_POLICY, "stop_atr_mult": 1.5, "stop_floor": 1.5,
                               "fast_cut": False, "grace_min": 10, "hold_cap": 60,
                               "lock_gain": 0.8, "cap_trend_extend": True,
                               "lock_confirm_mom": True},
}


def _day_indicators(bars: list[dict]) -> list[dict]:
    """Precompute _intraday_indicators for every bar index once — shared across
    all decisions and variants of a symbol-day (turns the A/B sweep from
    O(decisions × variants × bars²) into O(bars²) per symbol-day)."""
    from app.services.backtest_service import _intraday_indicators
    return [_intraday_indicators(bars, i) for i in range(len(bars))]


def _simulate_policy(bars: list[dict], inds: list[dict], entry_idx: int,
                     policy: dict) -> Optional[float]:
    """Simulate a long entered at bars[entry_idx].open under `policy`. Baseline
    knobs replicate _tech_signal's live LONG rules bar-for-bar. Returns net pnl%
    including slippage + charges, or None if unsimulatable."""
    from app.utils.trade_costs import buy_fill, sell_fill, charges

    if entry_idx >= len(bars):
        return None
    entry = buy_fill(bars[entry_idx]["open"])
    if entry <= 0:
        return None
    qty = max(1, int(50_000 / entry))          # session-default sizing; pnl% is size-invariant

    hold_cap  = int(policy.get("hold_cap", 30))
    grace_min = int(policy.get("grace_min", 0))
    trail     = policy.get("trail")
    hwm       = entry

    exit_price: Optional[float] = None
    last_i = entry_idx
    for i in range(entry_idx, len(bars)):
        last_i = i
        c = bars[i]
        price = c["close"]
        hwm   = max(hwm, price)
        held  = i - entry_idx
        try:
            h, m = int(c["time"].split(":")[0]), int(c["time"].split(":")[1])
        except (KeyError, ValueError, IndexError):
            h, m = 0, 0

        ind      = inds[i]
        gain     = (price - entry) / entry * 100
        atr_pct  = max(0.5, min(2.5, (ind.get("atr", 0.0) / price * 100) if price else 0.8))
        stop     = -max(policy["stop_floor"], policy["stop_atr_mult"] * atr_pct)
        take     = max(policy["take_floor"], policy["take_atr_mult"] * atr_pct)
        sma5, sma20, mom5 = ind.get("sma5", 0.0), ind.get("sma20", 0.0), ind.get("mom5", 0.0)

        # Hold cap. With cap_trend_extend (AUTO mode), hold_cap is a REVIEW
        # point, not a wall: a profitable position in an intact uptrend
        # (price ≥ SMA5 ≥ SMA20) keeps riding until the trend breaks or the
        # square-off — mirrors the session runner's auto hold review
        # (2026-07-10). Without the knob (manual caps), it's a hard flat cap.
        capped = held >= hold_cap
        if (capped and policy.get("cap_trend_extend")
                and gain > 0 and price >= sma5 >= sma20):
            capped = False
        if capped or (h * 60 + m) >= _SQUAREOFF_MIN:
            exit_price = sell_fill(price)
            break

        if held < grace_min:
            # Grace period: only a disaster stop (2×) can fire — everything else
            # waits for the position to establish (1-min noise wicks out normal
            # stops in the first minutes).
            if gain <= 2 * stop:
                exit_price = sell_fill(price)
                break
            continue
        if gain <= stop or gain >= take:
            exit_price = sell_fill(price)
            break
        if trail == "sma5":
            # lock_confirm_mom: the trail break must come with momentum already
            # down (mom5 < 0) — a one-bar pause under SMA5 in a rising stock no
            # longer cashes out the position (2026-07-10).
            if (gain >= policy["lock_gain"] and price < sma5
                    and (mom5 < 0 or not policy.get("lock_confirm_mom"))):
                exit_price = sell_fill(price)
                break
        elif trail == "hwm_atr":
            if gain >= policy["lock_gain"] and price < hwm * (1 - atr_pct / 100):
                exit_price = sell_fill(price)
                break
        if policy.get("fast_cut") and gain < 0.5:
            if (sma5 < sma20 and mom5 < -0.15) or mom5 < -0.30:
                exit_price = sell_fill(price)
                break
        if policy.get("rsi_exit") and ind.get("rsi", 50.0) > 75 and mom5 < 0:
            exit_price = sell_fill(price)
            break
    if exit_price is None:                      # ran off the end of the day's bars
        exit_price = sell_fill(bars[last_i]["close"])

    fees = charges(entry, exit_price, qty)
    pnl  = qty * (exit_price - entry) - fees
    return round(pnl / (qty * entry) * 100, 3)


def _simulate_long(bars: list[dict], entry_idx: int,
                   max_hold_minutes: Optional[int] = None) -> Optional[float]:
    """Live-policy simulation (the CF label): what the running sessions would
    actually do. None = the live AUTO policy (trend-extended hold review). An
    explicit max_hold_minutes is a MANUAL hard cap — sessions with a user-set
    cap force-exit flat at N minutes, so the label must too."""
    if max_hold_minutes is None:
        policy = LIVE_POLICY
    else:
        policy = {**LIVE_POLICY, "hold_cap": max_hold_minutes, "cap_trend_extend": False}
    return _simulate_policy(bars, _day_indicators(bars), entry_idx, policy)


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
        # Indicators once per symbol-day, shared by every decision's simulation.
        inds = (await asyncio.to_thread(_day_indicators, bars)) if bars else []

        updates = []
        for r in day_rows:
            cf = None
            if bars:
                idx = _bar_index_for_time(bars, r[3])
                if idx is not None and idx + 1 < len(bars):
                    # Enter at the NEXT bar's open — the decision is made on the
                    # closed candle, exactly like the live entry path.
                    cf = _simulate_policy(bars, inds, idx + 1, LIVE_POLICY)
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


# ── Exit-policy A/B evaluation ────────────────────────────────────────────────
# Two entry populations, tagged by `source`:
#   sessions — the day's labeled session decisions (what the live system faced)
#   store    — synthesized entries at a fixed stride over EVERY recorded
#              symbol-day in the tick store. Needs no sessions/ensemble, so the
#              whole recorded history becomes A/B evidence immediately instead
#              of waiting for live days to accumulate.

_AB_TRAINED_KEY   = "counterfactual:ab_trained:"          # + date → dedupe flag
_AB_STORE_KEY     = "counterfactual:ab_store_trained:"    # + date → dedupe flag
_AB_MAX_PER_DAY   = 4000                                  # decision sample cap per day
_AB_STORE_STRIDE  = 3        # store population: an entry every N minutes
_AB_STORE_LAST_ENTRY_MIN = 13 * 60 + 30   # entries up to 13:30 IST — matches the
                                          # live entry-cutoff regime the winning
                                          # policy would actually trade under

_AB_DDL = """CREATE TABLE IF NOT EXISTS cf_exit_ab (
    day         TEXT NOT NULL,
    variant     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'sessions',
    n           INTEGER,
    wins        INTEGER,
    avg_pnl_pct DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (day, variant, source)
)"""


async def _ensure_ab_schema(conn) -> None:
    """Create/migrate cf_exit_ab: older deployments lack the `source` column and
    have PK (day, variant) — widen both. Idempotent."""
    from sqlalchemy import text
    await conn.execute(text(_AB_DDL))
    await conn.execute(text(
        "ALTER TABLE cf_exit_ab ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'sessions'"))
    await conn.execute(text("ALTER TABLE cf_exit_ab DROP CONSTRAINT IF EXISTS cf_exit_ab_pkey"))
    await conn.execute(text(
        "ALTER TABLE cf_exit_ab ADD CONSTRAINT cf_exit_ab_pkey PRIMARY KEY (day, variant, source)"))


async def _store_ab_results(day: str, source: str, stats: dict[str, list[float]]) -> dict:
    """Aggregate per-variant pnl lists and upsert into cf_exit_ab."""
    from sqlalchemy import text
    from app.database.postgres import engine
    out: dict[str, dict] = {}
    async with engine.begin() as conn:
        await _ensure_ab_schema(conn)
        for name, pnls in stats.items():
            if not pnls:
                continue
            agg = {"n": len(pnls),
                   "wins": sum(1 for p in pnls if p >= 0),
                   "avg": round(sum(pnls) / len(pnls), 4)}
            out[name] = agg
            await conn.execute(text("""
                INSERT INTO cf_exit_ab (day, variant, source, n, wins, avg_pnl_pct, updated_at)
                VALUES (:day, :v, :src, :n, :w, :avg, NOW())
                ON CONFLICT (day, variant, source) DO UPDATE SET
                    n = EXCLUDED.n, wins = EXCLUDED.wins,
                    avg_pnl_pct = EXCLUDED.avg_pnl_pct, updated_at = NOW()
            """), {"day": day, "v": name, "src": source,
                   "n": agg["n"], "w": agg["wins"], "avg": agg["avg"]})
    return out


async def run_exit_ab(day: str) -> dict:
    """Evaluate every EXIT_VARIANT on the SAME entry population — the day's
    labeled decisions — against the recorded bars, and store per-variant
    aggregates in cf_exit_ab. This is the offline policy evaluation that decides
    the live exit-policy change with data instead of intuition. Idempotent per
    day. Returns {variant: {n, wins, avg_pnl_pct}}."""
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.utils.redis_client import get_redis
    from app.data.candle_store import read_bars

    r = get_redis()
    flag = _AB_TRAINED_KEY + day
    if await r.get(flag):
        return {}

    async with engine.begin() as conn:
        await _ensure_ab_schema(conn)
        rows = (await conn.execute(text("""
            SELECT d.symbol, d.candle_time
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.cf_pnl_pct IS NOT NULL
              AND COALESCE(sm.date,
                           (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) = :day
            ORDER BY d.id
        """), {"day": day})).fetchall()
    if not rows:
        return {}
    if len(rows) > _AB_MAX_PER_DAY:              # even stride-sample, stable per day
        step = len(rows) / _AB_MAX_PER_DAY
        rows = [rows[int(i * step)] for i in range(_AB_MAX_PER_DAY)]

    bars_cache: dict[str, tuple[list[dict], list[dict]]] = {}
    stats: dict[str, list[float]] = {v: [] for v in EXIT_VARIANTS}
    for sym, hhmm in rows:
        sym = sym.upper()
        if sym not in bars_cache:
            bars = await asyncio.to_thread(read_bars, sym, day, 60)
            inds = (await asyncio.to_thread(_day_indicators, bars)) if bars else []
            bars_cache[sym] = (bars, inds)
        bars, inds = bars_cache[sym]
        if not bars:
            continue
        idx = _bar_index_for_time(bars, hhmm)
        if idx is None or idx + 1 >= len(bars):
            continue
        for name, policy in EXIT_VARIANTS.items():
            pnl = _simulate_policy(bars, inds, idx + 1, policy)
            if pnl is not None:
                stats[name].append(pnl)

    out = await _store_ab_results(day, "sessions", stats)
    await r.set(flag, "1", ex=86400 * 7)
    logger.info("exit A/B evaluated for %s: %s", day,
                {k: f"{v['wins']}/{v['n']} ({v['avg']:+.3f}%)" for k, v in out.items()},
                extra={"log_type": "ai_engine", "event": "cf_exit_ab", "day": day})
    return out


async def run_exit_ab_store(day: str, force: bool = False) -> dict:
    """A/B over the ENTIRE tick store for `day`: every symbol with usable
    coverage contributes a synthesized entry every _AB_STORE_STRIDE minutes from
    the open through the 13:30 entry cutoff — the same regime the winning policy
    would trade under live. No sessions or ensemble required, so all recorded
    history becomes evidence immediately. Idempotent per day (Redis flag) unless
    force=True. Returns {variant: {n, wins, avg}}."""
    from app.utils.redis_client import get_redis
    from app.data.candle_store import symbols_with_ticks, day_coverage, read_bars

    r = get_redis()
    flag = _AB_STORE_KEY + day
    if not force and await r.get(flag):
        return {}

    symbols = await asyncio.to_thread(symbols_with_ticks, day)
    stats: dict[str, list[float]] = {v: [] for v in EXIT_VARIANTS}
    used_symbols = 0
    for sym in symbols:
        cov = await asyncio.to_thread(day_coverage, sym, day)
        if cov.get("ticks", 0) < _MIN_DAY_TICKS:
            continue
        bars = await asyncio.to_thread(read_bars, sym, day, 60)
        if len(bars) < 40:
            continue
        inds = await asyncio.to_thread(_day_indicators, bars)
        used_symbols += 1
        for i in range(1, len(bars) - 1, _AB_STORE_STRIDE):
            try:
                h, m = int(bars[i]["time"].split(":")[0]), int(bars[i]["time"].split(":")[1])
            except (KeyError, ValueError, IndexError):
                continue
            if (h * 60 + m) > _AB_STORE_LAST_ENTRY_MIN:
                break
            for name, policy in EXIT_VARIANTS.items():
                pnl = _simulate_policy(bars, inds, i + 1, policy)
                if pnl is not None:
                    stats[name].append(pnl)

    if not any(stats.values()):
        return {}
    out = await _store_ab_results(day, "store", stats)
    await r.set(flag, "1", ex=86400 * 14)
    logger.info("exit A/B (store) evaluated %s across %d symbols: %s", day, used_symbols,
                {k: f"{v['wins']}/{v['n']} ({v['avg']:+.3f}%)" for k, v in out.items()},
                extra={"log_type": "ai_engine", "event": "cf_exit_ab_store",
                       "day": day, "symbols": used_symbols})
    return out


async def exit_ab_report(days: int = 14) -> dict:
    """Aggregated A/B results for the API: per-variant totals over the last N
    days plus the per-day rows — the evidence table for changing the live exit
    policy."""
    from sqlalchemy import text
    from app.database.postgres import engine
    try:
        async with engine.begin() as conn:
            await _ensure_ab_schema(conn)
            daily = (await conn.execute(text("""
                SELECT day, variant, source, n, wins, avg_pnl_pct
                FROM cf_exit_ab
                WHERE day >= (NOW() AT TIME ZONE 'Asia/Kolkata' - make_interval(days => :d))::date::text
                ORDER BY day DESC, source, avg_pnl_pct DESC
            """), {"d": days})).fetchall()
        totals: dict[str, dict] = {}
        for day, variant, source, n, wins, avg in daily:
            t = totals.setdefault(variant, {"n": 0, "wins": 0, "pnl_sum": 0.0})
            t["n"] += n or 0
            t["wins"] += wins or 0
            t["pnl_sum"] += (avg or 0.0) * (n or 0)
        summary = [
            {"variant": v, "n": t["n"],
             "win_rate": round(t["wins"] / t["n"], 3) if t["n"] else 0.0,
             "avg_pnl_pct": round(t["pnl_sum"] / t["n"], 4) if t["n"] else 0.0,
             "policy": EXIT_VARIANTS.get(v, {})}
            for v, t in totals.items()
        ]
        summary.sort(key=lambda x: x["avg_pnl_pct"], reverse=True)
        return {
            "summary": summary,
            "daily": [{"day": d, "variant": v, "source": s, "n": n, "wins": w, "avg_pnl_pct": a}
                      for d, v, s, n, w, a in daily],
        }
    except Exception as exc:
        logger.warning("exit_ab_report failed: %s", exc)
        return {"summary": [], "daily": []}


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


async def _sync_learning_rates(trigger: str) -> None:
    """Publish per-agent per-action weights (learning.py) to Redis. Previously
    this only happened when a REAL trade closed — with zero live trades since
    2026-06-30 the key sat empty (1h TTL) and the ensemble's per-action vote
    weighting silently ran at 1.0x for every agent."""
    try:
        from app.agents import get_learning
        await get_learning()._sync_weights_to_redis()   # also syncs action rates
        logger.info("agent action-rate weights synced",
                    extra={"log_type": "ai_engine", "event": "action_rates_synced",
                           "trigger": trigger})
    except Exception as exc:
        logger.warning("action-rate sync failed (%s): %s", trigger, exc)


async def counterfactual_loop() -> None:
    """Off-hours: label pending decisions against the tick store, then run the
    RL and memory consumers for the last completed trading day (each is
    idempotent per day, so re-running is free)."""
    global _last_sweep
    await asyncio.sleep(40)
    await init_schema()
    logger.info("counterfactual loop started",
                extra={"log_type": "app_lifecycle", "event": "cf_loop_started"})
    # Startup sync so a restart never leaves the ensemble without action rates.
    await _sync_learning_rates("startup")
    while True:
        try:
            if not _market_hours() and (time.time() - _last_sweep) >= _SWEEP_EVERY:
                _last_sweep = time.time()
                await label_pending()
                day = _last_completed_trading_day(datetime.now(IST))
                await train_rl_from_labels(day)
                await promote_memory_phantoms(day)
                await run_exit_ab(day)
                await run_exit_ab_store(day)   # whole-tick-store population too
                # Fresh CF labels change the per-action correctness rates —
                # republish so the next session runs on current weights.
                await _sync_learning_rates("cf_sweep")
        except Exception as exc:
            logger.warning("counterfactual sweep failed: %s", exc)
        await asyncio.sleep(60)
