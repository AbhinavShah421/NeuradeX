"""Learning System — stores predictions in PostgreSQL, updates agent weights from outcomes."""
from __future__ import annotations
import json
from typing import Optional
from datetime import datetime
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_WEIGHTS_KEY      = "ai_engine:agent_weights"
_ACTION_RATES_KEY = "ai_engine:agent_action_rates"
_MIN_ACTION_SAMPLES = 20   # minimum decisions before we trust action-specific rate
_SHRINK_K           = 20   # Bayesian shrinkage prior weight (in pseudo-samples)
_CF_RATE_WEIGHT     = 0.5  # counterfactual samples count at half a real outcome

# Exclude outcomes from operator-run replay/backtest sessions from the stats
# that drive live weights AND the agent-accuracy UI (2026-07-09). Those
# outcomes depend on which symbols/dates an operator happened to test and on
# past exit-policy bugs (e.g. the hold-cap parity bug that lost -1.5% on a batch
# of large caps), so pooling them dragged agents' displayed BUY accuracy from
# ~100% on real paper trades to ~34%, and — worse — degraded the live per-action
# weights. Keeps paper/live real outcomes and manual-analysis predictions (no
# session); systematic counterfactual labels are merged in separately at
# _CF_RATE_WEIGHT. Mirrors the pattern-memory source weighting.
_EXCLUDE_SIM_OUTCOMES = """
    AND NOT EXISTS (
        SELECT 1 FROM session_metadata sm
        WHERE sm.session_id = p.context::jsonb->>'session_id'
          AND sm.mode IN ('replay', 'backtest')
    )
"""

# Weight decay/normalization (see record_outcome). Additive weight updates with a
# [0.3, 3.0] clamp and no decay saturated 9 of 12 agents at the ceiling — learned
# weighting collapsed back to uniform. After every outcome we (a) decay each weight
# toward 1.0 with a ~200-trade half-life so stale evidence fades, and (b) renormalize
# the mean to 1.0 so the *relative* ordering is what matters, not absolute drift.
_DECAY_PER_TRADE = 0.99654   # 0.5 ** (1/200) → half-life ≈ 200 recorded outcomes


def _shrunk_rate(correct: int, total: int, base: float, k: int = _SHRINK_K) -> float:
    """Bayesian-shrunk accuracy: pulls small samples toward the base rate so an
    agent needs sustained evidence (not a lucky 20-trade run) to earn a big
    factor. (correct + k·base) / (total + k)."""
    if total <= 0:
        return base
    return (correct + k * base) / (total + k)


def _vote_was_correct(action: str, trade_won: bool) -> bool:
    """Whether an agent's vote was right given the executed trade's result.
    The system trades long-only (BUY→SELL), so:
      BUY  voter is right when the trade won;
      SELL voter is right when the trade lost;
      HOLD voter (abstain) is right when the trade lost."""
    if action == "BUY":
        return trade_won
    return not trade_won

_DDL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS ai_engine_predictions (
    id              SERIAL PRIMARY KEY,
    prediction_id   VARCHAR(36) UNIQUE NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    candle_time     VARCHAR(10),
    final_action    VARCHAR(10) NOT NULL,
    final_confidence FLOAT NOT NULL,
    agent_agreement FLOAT,
    risk_score      FLOAT,
    agent_signals   TEXT NOT NULL,
    rl_state        INT,
    fingerprint     TEXT,
    context         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
)""",
    # Older deployments created the table before fingerprinting existed
    "ALTER TABLE ai_engine_predictions ADD COLUMN IF NOT EXISTS fingerprint TEXT",
    """CREATE TABLE IF NOT EXISTS ai_engine_outcomes (
    id              SERIAL PRIMARY KEY,
    prediction_id   VARCHAR(36),
    symbol          VARCHAR(20),
    entry_price     FLOAT,
    exit_price      FLOAT,
    pnl             FLOAT,
    pnl_pct         FLOAT,
    reward          FLOAT,
    outcome         VARCHAR(10),
    created_at      TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS ai_engine_agent_weights (
    agent_name          VARCHAR(50) PRIMARY KEY,
    weight              FLOAT DEFAULT 1.0,
    total_predictions   INT DEFAULT 0,
    correct_predictions INT DEFAULT 0,
    total_reward        FLOAT DEFAULT 0.0,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
)""",
    # All agents — original 7 + the 4 newer models.  ON CONFLICT DO NOTHING so
    # existing learned weights are never clobbered on restart.
    """INSERT INTO ai_engine_agent_weights (agent_name, weight)
VALUES ('technical',1.0),('pattern',1.0),('momentum',1.0),
       ('volatility',1.0),('sentiment',1.0),('rl',0.8),('memory',1.3),
       ('meanrev',0.9),('regime',0.6),('anomaly',0.7),('gbm',1.1)
ON CONFLICT DO NOTHING""",
]


class LearningSystem:
    """Stores predictions and updates per-agent weights from trade outcomes."""

    async def init_db(self) -> None:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))
            logger.info("AI engine DB tables ready",
                        extra={"log_type": "ai_engine", "event": "db_init"})
        except Exception as exc:
            logger.warning("AI engine DB init failed: %s", exc)

    # ── Store prediction ──────────────────────────────────────────────────────

    async def store_prediction(
        self,
        decision,
        candle_time: str,
        context: dict,
        rl_state: Optional[int] = None,
        fingerprint: Optional[list] = None,
    ) -> None:
        signals = [
            {"agent": s.agent_name, "action": s.action,
             "confidence": s.confidence, "weight": s.weight,
             "reasoning": s.reasoning, "indicators": s.indicators}
            for s in decision.agents
        ]
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                # RETURNING lets us reliably detect if the row was inserted
                # (ON CONFLICT DO NOTHING returns nothing if the row already exists)
                result = await conn.execute(text("""
                    INSERT INTO ai_engine_predictions
                      (prediction_id, symbol, timestamp, candle_time, final_action,
                       final_confidence, agent_agreement, risk_score, agent_signals,
                       rl_state, fingerprint, context)
                    VALUES (:pid,:sym,:ts,:ct,:fa,:fc,:ag,:rs,:sigs,:rls,:fp,:ctx)
                    ON CONFLICT (prediction_id) DO NOTHING
                    RETURNING prediction_id
                """), {
                    "pid": decision.prediction_id,
                    "sym": context.get("symbol", ""),
                    "ts":  decision.timestamp or datetime.now(),
                    "ct":  candle_time,
                    "fa":  decision.action,
                    "fc":  decision.confidence,
                    "ag":  decision.agent_agreement,
                    "rs":  decision.risk_score,
                    "sigs": json.dumps(signals),
                    "rls": rl_state,
                    "fp":  json.dumps(fingerprint) if fingerprint else None,
                    "ctx": json.dumps(context),
                })
                inserted = result.fetchone()
                if inserted:
                    for sig in signals:
                        await conn.execute(text("""
                            UPDATE ai_engine_agent_weights
                            SET total_predictions = total_predictions + 1,
                                updated_at = NOW()
                            WHERE agent_name = :name
                        """), {"name": sig["agent"]})
        except Exception as exc:
            logger.warning("store_prediction failed: %s", exc)

    # ── Record outcome + update weights ──────────────────────────────────────

    async def record_outcome(
        self,
        prediction_id: str,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
    ) -> float:
        reward  = self._reward(pnl_pct)
        outcome = "correct" if pnl >= 0 else "wrong"
        mem_fp: Optional[str] = None      # fingerprint json to promote into memory
        mem_action: str = "HOLD"
        rl_state: Optional[int] = None
        rl_action: Optional[str] = None
        row = None                        # defined here so a DB failure inside the
        signals: list = []                # try can't NameError the blocks below
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                # Store outcome
                await conn.execute(text("""
                    INSERT INTO ai_engine_outcomes
                      (prediction_id, symbol, entry_price, exit_price, pnl, pnl_pct, reward, outcome)
                    VALUES (:pid,:sym,:ep,:xp,:pnl,:pp,:rw,:oc)
                """), {"pid": prediction_id, "sym": symbol,
                       "ep": entry_price, "xp": exit_price,
                       "pnl": pnl, "pp": pnl_pct, "rw": reward, "oc": outcome})

                # Get the agent signals + fingerprint + rl_state for this prediction
                row = (await conn.execute(
                    text("SELECT agent_signals, fingerprint, final_action, rl_state "
                         "FROM ai_engine_predictions WHERE prediction_id=:pid"),
                    {"pid": prediction_id}
                )).fetchone()

                if row:
                    signals = json.loads(row[0])
                    mem_fp, mem_action = row[1], row[2]
                    rl_state = row[3]

                    # ── Outcome-driven weight update ──────────────────────────
                    # reward > 0 → the BUY was right; reward < 0 → it was wrong.
                    # Each agent is judged by whether its own signal matched that
                    # outcome, NOT by whether it agreed with the top-confidence agent.
                    #
                    # The system only trades BUY→SELL, so:
                    #   Agent said BUY  → correct when trade won  (reward > 0)
                    #   Agent said SELL → correct when trade lost (reward < 0)
                    #   Agent said HOLD → abstained; receives a soft signal
                    #
                    # Delta is scaled by |reward| so a 5% winner updates weights
                    # more than a 0.1% winner — magnitude matters.
                    _LR = 0.06   # per-trade learning rate; weight bounds [0.3, 3.0]
                    for sig in signals:
                        act = sig["action"]
                        if act == "BUY":
                            # BUY was the correct call if the trade won
                            delta   = _LR * reward
                            correct = reward > 0
                        elif act == "SELL":
                            # SELL was the correct call if the trade lost
                            delta   = _LR * -reward
                            correct = reward < 0
                        else:
                            # HOLD = "I wouldn't enter this trade."
                            # When the trade loses, HOLD was correct — reward the
                            # agent so defensive agents (Memory cold-start, Anomaly
                            # veto, Sentiment no-signal) don't get demoted for being
                            # right. When the trade wins, HOLD missed a good entry —
                            # apply a small miss penalty.
                            if reward < 0:
                                delta   = _LR * abs(reward) * 0.5   # correct abstention
                                correct = True
                            else:
                                delta   = -_LR * reward * 0.15      # missed winning trade
                                correct = False

                        await conn.execute(text("""
                            UPDATE ai_engine_agent_weights
                            SET correct_predictions = correct_predictions + :corr,
                                total_reward        = total_reward + :rw,
                                weight              = GREATEST(0.3, LEAST(3.0, weight + :delta)),
                                updated_at          = NOW()
                            WHERE agent_name = :name
                        """), {
                            "corr":  1 if correct else 0,
                            "rw":    reward,
                            "delta": delta,
                            "name":  sig["agent"],
                        })
                        if sig["agent"] == "rl":
                            rl_action = sig["action"]

                    # ── Anti-saturation: decay + renormalize ──────────────────
                    # Additive deltas with a hard [0.3, 3.0] clamp and no decay
                    # eventually pinned most agents at the ceiling, collapsing the
                    # learned weighting back to uniform. Two counter-forces:
                    #   1. Decay each weight toward 1.0 (half-life ≈ 200 outcomes)
                    #      so evidence ages out instead of accumulating forever.
                    #   2. Renormalize the mean to 1.0 — only *relative* weight
                    #      matters in the vote, so absolute drift is pure noise
                    #      that eats headroom under the clamp.
                    await conn.execute(text("""
                        UPDATE ai_engine_agent_weights
                        SET weight = 1.0 + (weight - 1.0) * :decay
                    """), {"decay": _DECAY_PER_TRADE})
                    await conn.execute(text("""
                        UPDATE ai_engine_agent_weights
                        SET weight = GREATEST(0.3, LEAST(3.0,
                                weight / NULLIF((SELECT AVG(weight)
                                                 FROM ai_engine_agent_weights), 0)))
                    """))
        except Exception as exc:
            logger.warning("record_outcome failed: %s", exc)

        # Track sentiment-specific accuracy in Redis for dynamic gate calibration.
        # Stored as HASH ai_engine:sentiment_perf:{BUY|SELL} → {correct, total}.
        # SentimentAgent reads this to tune confidence/score thresholds at runtime.
        if row:
            sent_sig = next((s for s in signals if s.get("agent") == "sentiment"), None)
            if sent_sig and sent_sig.get("action") in ("BUY", "SELL"):
                act = sent_sig["action"]
                is_correct = (act == "BUY" and reward > 0) or (act == "SELL" and reward < 0)
                try:
                    from app.utils.redis_client import get_redis
                    r = get_redis()
                    key = f"ai_engine:sentiment_perf:{act}"
                    await r.hincrby(key, "total", 1)
                    if is_correct:
                        await r.hincrby(key, "correct", 1)
                    await r.expire(key, 86400 * 30)   # rolling 30-day window
                except Exception as exc:
                    logger.debug("sentiment perf tracking skipped: %s", exc)

        # Train the RL agent's Q-table from this outcome (every recorded trade,
        # regardless of caller — sessions, backtests, or the analyze→outcome flow).
        if rl_state is not None and rl_action:
            try:
                from app.agents import get_rl_agent
                from app.agents.rl_agent import ACTIONS
                action_idx = ACTIONS.index(rl_action) if rl_action in ACTIONS else 2
                # next_state = rl_state: we don't store the exit-candle state, so we reuse
                # the entry state. This means the Q-update is reward-only (no future value
                # bootstrapping across candles). A known simplification — to fix properly,
                # store the exit window's state in ai_engine_outcomes.
                await get_rl_agent().update(rl_state, action_idx, reward, rl_state)
            except Exception as exc:
                logger.debug("RL update skipped: %s", exc)

        # Promote this realised trade into the Pattern Memory bank so the next
        # similar situation can learn from how it actually turned out.
        if mem_fp:
            try:
                from app.agents import get_memory
                await get_memory().add_case(
                    symbol=symbol, fingerprint=json.loads(mem_fp), action=mem_action,
                    pnl_pct=pnl_pct, entry_price=entry_price, exit_price=exit_price,
                    source="LIVE",
                )
            except Exception as exc:
                logger.debug("memory promotion skipped: %s", exc)

        await self._sync_weights_to_redis()
        return reward

    # ── Weight management ─────────────────────────────────────────────────────

    async def get_weights(self) -> dict[str, float]:
        try:
            from app.utils.redis_client import cache_get
            raw = await cache_get(_WEIGHTS_KEY)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis weights cache miss: %s", exc)
        return await self._weights_from_db()

    async def _weights_from_db(self) -> dict[str, float]:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                rows = (await conn.execute(
                    text("SELECT agent_name, weight FROM ai_engine_agent_weights")
                )).fetchall()
            return {r[0]: float(r[1]) for r in rows}
        except Exception:
            logger.debug("Failed to load agent weights from DB", exc_info=True)
            return {}

    async def _sync_weights_to_redis(self) -> None:
        try:
            weights = await self._weights_from_db()
            from app.utils.redis_client import cache_set
            # 24h TTL: with the old 1h expiry the weights vanished on any quiet
            # day (sync used to be trade-close-triggered only).
            await cache_set(_WEIGHTS_KEY, json.dumps(weights), expire=86400)
        except Exception as exc:
            logger.debug("Could not sync weights to Redis: %s", exc)
        await self._sync_action_rates_to_redis()

    async def _sync_action_rates_to_redis(self) -> None:
        """Publish per-agent, per-action *vote correctness* rates + the base rates
        the ensemble should compare them against.

        Correctness is action-aware (see _vote_was_correct): a SELL voter is right
        when the trade LOST. The old query counted o.outcome='correct' (trade won)
        for every action, which inverted SELL — good bears were published with low
        rates and had their votes scaled DOWN.

        Each action also gets a baseline: BUY's is the base win rate of executed
        trades (~P(win)), SELL/HOLD's is its complement. The ensemble weights a
        vote by its LIFT over that baseline, not vs a fixed 0.5 — with a ~28% base
        win rate, comparing to 0.5 was silently halving every directional vote.

        Counterfactual outcomes (session decisions the system declined, labeled
        against the recorded tick data — see agents/counterfactual.py) are merged
        in at _CF_RATE_WEIGHT per sample, so the rates keep calibrating even on
        days the gates allow zero real trades, without simulated labels ever
        outvoting real ones.

        Published shape:
            {"base": {"BUY": 0.28, "SELL": 0.72, "HOLD": 0.72},
             "rates": {agent: {action: shrunk_correct_rate}}}
        """
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                base_row = (await conn.execute(text("""
                    SELECT COUNT(*)::int,
                           SUM(CASE WHEN o.outcome = 'correct' THEN 1 ELSE 0 END)::int
                    FROM ai_engine_outcomes o
                    JOIN ai_engine_predictions p USING (prediction_id)
                    WHERE TRUE
                """ + _EXCLUDE_SIM_OUTCOMES))).fetchone()
                rows = (await conn.execute(text("""
                    SELECT
                        sig->>'agent'  AS agent_name,
                        sig->>'action' AS action,
                        COUNT(*)::int  AS total,
                        SUM(CASE
                              WHEN sig->>'action' = 'BUY'
                                   THEN CASE WHEN o.outcome = 'correct' THEN 1 ELSE 0 END
                              ELSE CASE WHEN o.outcome = 'correct' THEN 0 ELSE 1 END
                            END)::int AS correct
                    FROM ai_engine_predictions p
                    JOIN ai_engine_outcomes o USING (prediction_id)
                    CROSS JOIN LATERAL jsonb_array_elements(p.agent_signals::jsonb) AS sig
                    WHERE sig->>'agent' IS NOT NULL
                """ + _EXCLUDE_SIM_OUTCOMES + """
                    GROUP BY sig->>'agent', sig->>'action'
                """))).fetchall()

                # Counterfactual base + per-agent counts. Guarded: the cf columns
                # only exist once the counterfactual module has initialised.
                cf_base_row, cf_rows = None, []
                try:
                    cf_base_row = (await conn.execute(text("""
                        SELECT COUNT(*)::int,
                               SUM(CASE WHEN cf_pnl_pct >= 0 THEN 1 ELSE 0 END)::int
                        FROM session_decisions
                        WHERE cf_pnl_pct IS NOT NULL AND executed = FALSE
                    """))).fetchone()
                    cf_rows = (await conn.execute(text("""
                        SELECT
                            sig->>'agent'  AS agent_name,
                            sig->>'action' AS action,
                            COUNT(*)::int  AS total,
                            SUM(CASE
                                  WHEN sig->>'action' = 'BUY'
                                       THEN CASE WHEN d.cf_pnl_pct >= 0 THEN 1 ELSE 0 END
                                  ELSE CASE WHEN d.cf_pnl_pct >= 0 THEN 0 ELSE 1 END
                                END)::int AS correct
                        FROM session_decisions d
                        CROSS JOIN LATERAL jsonb_array_elements(d.agents) AS sig
                        WHERE d.cf_pnl_pct IS NOT NULL AND d.executed = FALSE
                          AND sig->>'agent' IS NOT NULL
                        GROUP BY sig->>'agent', sig->>'action'
                    """))).fetchall()
                except Exception:
                    cf_base_row, cf_rows = None, []   # cf schema not initialised yet

            # Merged base rate: real outcomes at full weight, cf at _CF_RATE_WEIGHT.
            n_out, n_win = float(base_row[0] or 0), float(base_row[1] or 0)
            if cf_base_row:
                n_out += _CF_RATE_WEIGHT * float(cf_base_row[0] or 0)
                n_win += _CF_RATE_WEIGHT * float(cf_base_row[1] or 0)
            base_win = (n_win / n_out) if n_out > 0 else 0.5
            base = {"BUY": round(base_win, 3),
                    "SELL": round(1.0 - base_win, 3),
                    "HOLD": round(1.0 - base_win, 3)}

            # Merged per-agent counts.
            counts: dict[tuple[str, str], list[float]] = {}
            for r in rows:
                counts[(r[0], r[1])] = [float(r[2] or 0), float(r[3] or 0)]
            for r in cf_rows:
                cur = counts.setdefault((r[0], r[1]), [0.0, 0.0])
                cur[0] += _CF_RATE_WEIGHT * float(r[2] or 0)
                cur[1] += _CF_RATE_WEIGHT * float(r[3] or 0)

            rates: dict[str, dict[str, float]] = {}
            for (agent, action), (total, correct) in counts.items():
                if total >= _MIN_ACTION_SAMPLES and action in base:
                    rates.setdefault(agent, {})[action] = round(
                        _shrunk_rate(correct, total, base[action]), 3)

            from app.utils.redis_client import cache_set
            payload = {"base": base, "rates": rates}
            await cache_set(_ACTION_RATES_KEY, json.dumps(payload), expire=86400)
            logger.debug("Action rates synced: %d agents (base win %.3f)", len(rates), base_win)
        except Exception as exc:
            logger.debug("Could not sync action rates to Redis: %s", exc)

    # ── Performance stats ─────────────────────────────────────────────────────

    async def get_performance(self) -> list[dict]:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                # Ensure every registered agent has a row so the UI always shows
                # all agents, even newly added ones with no completed trades yet.
                from app.agents.ensemble import DEFAULT_WEIGHTS
                for agent_name, default_w in DEFAULT_WEIGHTS.items():
                    await conn.execute(text("""
                        INSERT INTO ai_engine_agent_weights
                            (agent_name, weight, total_predictions, correct_predictions, total_reward)
                        VALUES (:n, :w, 0, 0, 0.0)
                        ON CONFLICT (agent_name) DO NOTHING
                    """), {"n": agent_name, "w": default_w})

                rows = (await conn.execute(text("""
                    SELECT agent_name, weight, total_predictions,
                           correct_predictions, total_reward
                    FROM ai_engine_agent_weights
                    ORDER BY weight DESC
                """))).fetchall()

                # Per-agent, per-action breakdown from the predictions + outcomes tables.
                # jsonb_array_elements unnests the agent_signals JSON array so each agent
                # vote is its own row, then we group by agent+action to get BUY/SELL/HOLD
                # counts and accuracy. Correctness is action-aware (same rule as the
                # weight updates + published rates): SELL/HOLD voters are right when
                # the executed trade LOST, BUY voters when it won.
                action_rows = (await conn.execute(text("""
                    SELECT
                        sig->>'agent'  AS agent_name,
                        sig->>'action' AS action,
                        COUNT(*)::int  AS total,
                        SUM(CASE
                              WHEN sig->>'action' = 'BUY'
                                   THEN CASE WHEN o.outcome = 'correct' THEN 1 ELSE 0 END
                              ELSE CASE WHEN o.outcome = 'correct' THEN 0 ELSE 1 END
                            END)::int AS correct,
                        ROUND(AVG(o.pnl_pct)::numeric, 2) AS avg_pnl
                    FROM ai_engine_predictions p
                    JOIN ai_engine_outcomes o USING (prediction_id)
                    CROSS JOIN LATERAL jsonb_array_elements(p.agent_signals::jsonb) AS sig
                    WHERE sig->>'agent' IS NOT NULL
                """ + _EXCLUDE_SIM_OUTCOMES + """
                    GROUP BY sig->>'agent', sig->>'action'
                    ORDER BY sig->>'agent', sig->>'action'
                """))).fetchall()

            # Index by_action per agent
            by_action_map: dict[str, list[dict]] = {}
            for r in action_rows:
                aname, action, total, correct, avg_pnl = r[0], r[1], r[2] or 0, r[3] or 0, float(r[4] or 0)
                by_action_map.setdefault(aname, []).append({
                    "action":    action,
                    "total":     total,
                    "correct":   correct,
                    "rate":      round(correct / total, 3) if total > 0 else 0.0,
                    "avg_pnl":   avg_pnl,
                })

            # Fetch registry overrides — these take precedence over the DB weight
            # in the ensemble, so Pattern Memory must show the override weight too.
            registry_overrides: dict[str, float] = {}
            try:
                from app.agents.registry import get_registry, weight_override
                reg = await get_registry()
                for agent_name in [r[0] for r in rows]:
                    ov = weight_override(reg, agent_name)
                    if ov is not None:
                        registry_overrides[agent_name] = ov
            except Exception:
                logger.debug("Failed to load registry weight overrides", exc_info=True)

            result = []
            for r in rows:
                total   = r[2] or 0
                correct = r[3] or 0
                db_weight = round(float(r[1]), 3)
                # If a registry override exists, that's the effective weight the
                # ensemble actually uses — show it so Pattern Memory stays in sync.
                effective_weight = round(registry_overrides.get(r[0], db_weight), 3)
                result.append({
                    "agent":          r[0],
                    "weight":         effective_weight,
                    "weight_learned": db_weight,           # raw DB weight for reference
                    "weight_pinned":  r[0] in registry_overrides,
                    "total":          total,
                    "correct":        correct,
                    "accuracy":       round(correct / total, 3) if correct > 0 and total > 0 else 0.0,
                    "total_reward":   round(float(r[4]), 4),
                    "by_action":      by_action_map.get(r[0], []),
                })
            # Re-sort by effective weight (registry overrides can change the order)
            result.sort(key=lambda x: x["weight"], reverse=True)
            return result
        except Exception as exc:
            logger.warning("get_performance failed: %s", exc)
            return []

    async def get_recent_predictions(
        self, symbol: Optional[str] = None, limit: int = 20
    ) -> list[dict]:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                if symbol:
                    rows = (await conn.execute(text("""
                        SELECT p.prediction_id, p.symbol, p.candle_time,
                               p.final_action, p.final_confidence, p.agent_agreement,
                               p.risk_score, p.created_at,
                               o.pnl, o.pnl_pct, o.outcome, o.reward
                        FROM ai_engine_predictions p
                        LEFT JOIN ai_engine_outcomes o USING (prediction_id)
                        WHERE p.symbol = :sym
                        ORDER BY p.created_at DESC LIMIT :lim
                    """), {"sym": symbol.upper(), "lim": limit})).fetchall()
                else:
                    rows = (await conn.execute(text("""
                        SELECT p.prediction_id, p.symbol, p.candle_time,
                               p.final_action, p.final_confidence, p.agent_agreement,
                               p.risk_score, p.created_at,
                               o.pnl, o.pnl_pct, o.outcome, o.reward
                        FROM ai_engine_predictions p
                        LEFT JOIN ai_engine_outcomes o USING (prediction_id)
                        ORDER BY p.created_at DESC LIMIT :lim
                    """), {"lim": limit})).fetchall()

            cols = ["prediction_id", "symbol", "candle_time", "final_action",
                    "final_confidence", "agent_agreement", "risk_score", "created_at",
                    "pnl", "pnl_pct", "outcome", "reward"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.warning("get_recent_predictions failed: %s", exc)
            return []

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _reward(pnl_pct: float) -> float:
        """Map P&L % to a normalised reward in [-1, 1]."""
        if pnl_pct >  5.0: return  1.0
        if pnl_pct >  2.0: return  0.7
        if pnl_pct >  0.5: return  0.4
        if pnl_pct >  0.0: return  0.1
        if pnl_pct > -0.5: return -0.1
        if pnl_pct > -2.0: return -0.4
        if pnl_pct > -5.0: return -0.7
        return -1.0
