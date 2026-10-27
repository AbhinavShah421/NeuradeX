"""Per-agent retrieval of similar, resolved decisions in shadow mode.

The retrieved evidence is persisted beside each session decision for evaluation,
but never changes the action used by the trading system.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from .fingerprint import FINGERPRINT_DIM, cosine

IST = timezone(timedelta(hours=5, minutes=30))
_CACHE_TTL = 300.0
_MAX_CASES = 50_000
_MAX_NEIGHBORS = 50
_MIN_NEIGHBORS = 8
_MIN_SIMILARITY = 0.45
_MIN_EFFECTIVE_CASES = 8.0
_CAUTION_WIN_RATE = 0.45
_SOURCE_WEIGHT = {"paper": 1.0, "live": 1.0, "backtest": 0.3, "replay": 0.3}


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def build_shadow_report(
    current_agents: list[dict],
    fingerprint: list[float],
    cases: list[dict],
) -> dict:
    """Compare current signals with their own nearest labeled prior decisions."""
    if len(fingerprint) != FINGERPRINT_DIM:
        return {"mode": "shadow_only", "agents": []}

    q = np.asarray(fingerprint, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if not q_norm:
        return {"mode": "shadow_only", "agents": []}
    q /= q_norm

    grouped: dict[tuple[str, str], list[tuple[float, dict]]] = {}
    for case in cases:
        fp = case.get("fingerprint")
        if not isinstance(fp, list) or len(fp) != FINGERPRINT_DIM:
            continue
        similarity = case.get("_similarity")
        if similarity is None:
            similarity = cosine(fingerprint, fp)
        if similarity < _MIN_SIMILARITY:
            continue
        for past in case.get("agents", []):
            if not isinstance(past, dict):
                continue
            agent = str(past.get("agent") or past.get("agent_name") or "")
            action = str(past.get("action") or "").upper()
            if agent and action in ("BUY", "SELL", "HOLD"):
                grouped.setdefault((agent, action), []).append((similarity, case))

    result = []
    for current in current_agents:
        if not isinstance(current, dict):
            continue
        agent = str(current.get("agent") or current.get("agent_name") or "")
        action = str(current.get("action") or "").upper()
        if not agent or action not in ("BUY", "SELL", "HOLD"):
            continue

        neighbors = sorted(
            grouped.get((agent, action), []),
            key=lambda item: item[0],
            reverse=True,
        )[:_MAX_NEIGHBORS]
        if not neighbors:
            result.append({
                "agent": agent, "action": action, "shadow_action": action,
                "assessment": "insufficient_evidence", "neighbors": 0,
                "effective_neighbors": 0.0, "historical_accuracy": None,
                "avg_counterfactual_pnl_pct": None,
            })
            continue

        weighted_total = 0.0
        weighted_correct = 0.0
        weighted_pnl = 0.0
        for _, case in neighbors:
            mode = str(case.get("mode") or "replay").lower()
            weight = _SOURCE_WEIGHT.get(mode, 0.3)
            pnl_pct = float(case["pnl_pct"])
            correct = pnl_pct > 0 if action == "BUY" else pnl_pct < 0
            weighted_total += weight
            weighted_correct += weight if correct else 0.0
            weighted_pnl += weight * pnl_pct

        accuracy = weighted_correct / weighted_total if weighted_total else 0.0
        shadow_action = action
        assessment = "neutral"
        if weighted_total < _MIN_EFFECTIVE_CASES or len(neighbors) < _MIN_NEIGHBORS:
            assessment = "insufficient_evidence"
        elif accuracy < _CAUTION_WIN_RATE and action == "BUY":
            shadow_action = "HOLD"
            assessment = "caution"
        elif accuracy < _CAUTION_WIN_RATE:
            assessment = "caution"
        elif accuracy >= 0.55:
            assessment = "support"

        result.append({
            "agent": agent,
            "action": action,
            "shadow_action": shadow_action,
            "assessment": assessment,
            "neighbors": len(neighbors),
            "effective_neighbors": round(weighted_total, 2),
            "historical_accuracy": round(accuracy, 4),
            "avg_counterfactual_pnl_pct": round(weighted_pnl / weighted_total, 4)
            if weighted_total else None,
        })

    return {"mode": "shadow_only", "agents": result}


class AgentDecisionMemory:
    def __init__(self) -> None:
        self._mat: np.ndarray | None = None
        self._cases: list[dict] = []
        self._groups: dict[tuple[str, str], list[int]] = {}
        self._loaded_at = 0.0
        self._loaded_for_date: str | None = None
        self._refresh_lock = asyncio.Lock()

    async def _refresh(self, current_date: str) -> None:
        def is_fresh() -> bool:
            return (
                self._mat is not None
                and self._loaded_for_date == current_date
                and time.monotonic() - self._loaded_at < _CACHE_TTL
            )

        if is_fresh():
            return
        async with self._refresh_lock:
            if is_fresh():
                return

            from sqlalchemy import text
            from app.database.postgres import engine

            async with engine.begin() as conn:
                rows = (await conn.execute(text("""
                    SELECT d.fingerprint, d.cf_pnl_pct, d.agents, sm.mode
                    FROM session_decisions d
                    JOIN session_metadata sm ON sm.session_id = d.session_id
                    WHERE d.fingerprint IS NOT NULL
                      AND d.cf_pnl_pct IS NOT NULL
                      AND d.executed = FALSE
                      AND sm.completed_at IS NOT NULL
                      AND sm.date < :current_date
                    ORDER BY sm.date DESC, d.id DESC
                    LIMIT :max_cases
                """), {"current_date": current_date, "max_cases": _MAX_CASES})).fetchall()

            cases: list[dict] = []
            vectors: list[list[float]] = []
            groups: dict[tuple[str, str], list[int]] = {}
            for row in rows:
                fp = _json_value(row[0], None)
                agents = _json_value(row[2], [])
                if not isinstance(fp, list) or len(fp) != FINGERPRINT_DIM:
                    continue
                if not isinstance(agents, list):
                    continue
                try:
                    pnl_pct = float(row[1])
                    vector = [float(value) for value in fp]
                except (TypeError, ValueError):
                    continue
                case = {
                    "fingerprint": vector,
                    "pnl_pct": pnl_pct,
                    "mode": str(row[3] or "replay").lower(),
                    "agents": agents,
                }
                case_index = len(cases)
                cases.append(case)
                vectors.append(vector)
                for signal in agents:
                    if not isinstance(signal, dict):
                        continue
                    name = str(signal.get("agent") or signal.get("agent_name") or "")
                    action = str(signal.get("action") or "").upper()
                    if name and action in ("BUY", "SELL", "HOLD"):
                        groups.setdefault((name, action), []).append(case_index)

            if vectors:
                matrix = np.asarray(vectors, dtype=np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1e-9
                self._mat = matrix / norms
            else:
                self._mat = np.zeros((0, FINGERPRINT_DIM), dtype=np.float32)
            self._cases = cases
            self._groups = groups
            self._loaded_for_date = current_date
            self._loaded_at = time.monotonic()

    async def shadow(
        self,
        current_agents: list[dict],
        fingerprint: list[float] | None,
        current_date: str | None,
    ) -> dict:
        if not fingerprint or len(fingerprint) != FINGERPRINT_DIM:
            return {"mode": "shadow_only", "agents": []}

        date = current_date or datetime.now(IST).strftime("%Y-%m-%d")
        await self._refresh(date)
        if self._mat is None or not self._cases:
            return build_shadow_report(current_agents, fingerprint, [])

        q = np.asarray(fingerprint, dtype=np.float32)
        norm = np.linalg.norm(q)
        if not norm:
            return {"mode": "shadow_only", "agents": []}
        q /= norm

        # Restrict scoring to agents/actions present in this decision.
        selected: dict[int, float] = {}
        for current in current_agents:
            if not isinstance(current, dict):
                continue
            name = str(current.get("agent") or current.get("agent_name") or "")
            action = str(current.get("action") or "").upper()
            indices = np.asarray(self._groups.get((name, action), []), dtype=np.int64)
            if not len(indices):
                continue
            scores = self._mat[indices] @ q
            positions = np.flatnonzero(scores >= _MIN_SIMILARITY)
            if len(positions) > _MAX_NEIGHBORS:
                positions = positions[
                    np.argpartition(scores[positions], -_MAX_NEIGHBORS)[-_MAX_NEIGHBORS:]
                ]
            for position in positions:
                index = int(indices[position])
                selected[index] = float(scores[position])

        active_pairs = {
            (
                str(current.get("agent") or current.get("agent_name") or ""),
                str(current.get("action") or "").upper(),
            )
            for current in current_agents if isinstance(current, dict)
        }
        similar = [
            {
                **self._cases[index],
                "agents": [
                    signal for signal in self._cases[index]["agents"]
                    if (
                        str(signal.get("agent") or signal.get("agent_name") or ""),
                        str(signal.get("action") or "").upper(),
                    ) in active_pairs
                ],
                "_similarity": score,
            }
            for index, score in selected.items()
            if score >= _MIN_SIMILARITY
        ]
        return build_shadow_report(current_agents, fingerprint, similar)

    async def status(self) -> dict:
        from sqlalchemy import text
        from app.database.postgres import engine

        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT
                    signal->>'agent' AS agent,
                    signal->>'action' AS action,
                    signal->>'shadow_action' AS shadow_action,
                    COUNT(*)::int AS samples,
                    SUM(CASE
                        WHEN signal->>'action' = 'BUY' THEN
                            CASE WHEN d.cf_pnl_pct > 0 THEN 1 ELSE 0 END
                        ELSE CASE WHEN d.cf_pnl_pct < 0 THEN 1 ELSE 0 END
                    END)::int AS baseline_correct,
                    SUM(CASE
                        WHEN signal->>'shadow_action' = 'BUY' THEN
                            CASE WHEN d.cf_pnl_pct > 0 THEN 1 ELSE 0 END
                        ELSE CASE WHEN d.cf_pnl_pct < 0 THEN 1 ELSE 0 END
                    END)::int AS shadow_correct
                FROM session_decisions d
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(d.agent_memory_shadow->'agents', '[]'::jsonb)
                ) AS signal
                WHERE d.cf_pnl_pct IS NOT NULL
                  AND d.executed = FALSE
                  AND d.agent_memory_shadow IS NOT NULL
                GROUP BY signal->>'agent', signal->>'action', signal->>'shadow_action'
                ORDER BY signal->>'agent', signal->>'action'
            """))).fetchall()

        return {
            "mode": "shadow_only",
            "evaluated": [
                {
                    "agent": row[0],
                    "action": row[1],
                    "shadow_action": row[2],
                    "samples": int(row[3]),
                    "baseline_accuracy": round(int(row[4]) / int(row[3]), 4)
                    if row[3] else None,
                    "shadow_accuracy": round(int(row[5]) / int(row[3]), 4)
                    if row[3] else None,
                }
                for row in rows
            ],
        }


_memory: AgentDecisionMemory | None = None


def get_agent_decision_memory() -> AgentDecisionMemory:
    global _memory
    if _memory is None:
        _memory = AgentDecisionMemory()
    return _memory
