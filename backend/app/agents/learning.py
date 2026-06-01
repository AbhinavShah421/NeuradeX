"""Learning System — stores predictions in PostgreSQL, updates agent weights from outcomes."""
from __future__ import annotations
import json
from typing import Optional
from datetime import datetime
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_WEIGHTS_KEY = "ai_engine:agent_weights"

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
    """INSERT INTO ai_engine_agent_weights (agent_name, weight)
VALUES ('technical',1.0),('pattern',1.0),('momentum',1.0),
       ('volatility',1.0),('sentiment',1.0),('rl',0.8),('memory',1.3)
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
                    # Find the winning action (highest conf*weight)
                    best = max(signals, key=lambda s: s["confidence"] * s["weight"])
                    best_action = best["action"]

                    for sig in signals:
                        # Agent is correct if it agreed with the best agent AND trade was good,
                        # OR it recommended opposite AND trade was bad
                        agent_agreed_best = sig["action"] == best_action
                        correct = (agent_agreed_best and reward > 0) or (not agent_agreed_best and reward < 0)
                        delta   = 0.05 if correct else -0.05

                        await conn.execute(text("""
                            UPDATE ai_engine_agent_weights
                            SET correct_predictions = correct_predictions + :corr,
                                total_reward        = total_reward + :rw,
                                weight              = GREATEST(0.3, LEAST(2.5, weight + :delta)),
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
        except Exception as exc:
            logger.warning("record_outcome failed: %s", exc)

        # Train the RL agent's Q-table from this outcome (every recorded trade,
        # regardless of caller — sessions, backtests, or the analyze→outcome flow).
        if rl_state is not None and rl_action:
            try:
                from app.agents import get_rl_agent
                from app.agents.rl_agent import ACTIONS
                action_idx = ACTIONS.index(rl_action) if rl_action in ACTIONS else 2
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
            return {}

    async def _sync_weights_to_redis(self) -> None:
        try:
            weights = await self._weights_from_db()
            from app.utils.redis_client import cache_set
            await cache_set(_WEIGHTS_KEY, json.dumps(weights), expire=3600)
        except Exception as exc:
            logger.debug("Could not sync weights to Redis: %s", exc)

    # ── Performance stats ─────────────────────────────────────────────────────

    async def get_performance(self) -> list[dict]:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                rows = (await conn.execute(text("""
                    SELECT agent_name, weight, total_predictions,
                           correct_predictions, total_reward
                    FROM ai_engine_agent_weights
                    ORDER BY weight DESC
                """))).fetchall()
            result = []
            for r in rows:
                total   = r[2] or 0   # total analyses run
                correct = r[3] or 0   # correct among those with recorded outcomes
                # accuracy denominator: only predictions with a known outcome
                # (correct_predictions <= outcomes_recorded <= total)
                result.append({
                    "agent":        r[0],
                    "weight":       round(float(r[1]), 3),
                    "total":        total,
                    "correct":      correct,
                    "accuracy":     round(correct / total, 3) if correct > 0 and total > 0 else 0.0,
                    "total_reward": round(float(r[4]), 4),
                })
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
