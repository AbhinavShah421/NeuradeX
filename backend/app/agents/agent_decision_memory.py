"""Per-agent Redis retrieval, shadow evaluation, and guarded BUY suppression."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from .fingerprint import FINGERPRINT_DIM, cosine

IST = timezone(timedelta(hours=5, minutes=30))
_CACHE_TTL = 300.0
_MAX_CASES = 50_000
_MAX_CASES_PER_AGENT = 2_000
_MAX_NEIGHBORS = 50
_MIN_NEIGHBORS = 8
_MIN_SIMILARITY = 0.45
_MIN_EFFECTIVE_CASES = 8.0
_CAUTION_WIN_RATE = 0.45
_SOURCE_WEIGHT = {"paper": 1.0, "live": 1.0, "backtest": 0.3, "replay": 0.3}
_MIN_PROMOTION_SAMPLES = 200
_MIN_PROMOTION_DATES = 20
_MIN_KEPT_SAMPLES = 30
_CASES_KEY = "ai_engine:agent_decision_memory:cases:{}"
_ACTIVE_KEY = "ai_engine:agent_decision_memory:active:{}"
_ENABLED_KEY = "ai_engine:agent_decision_memory:enabled"
_AGENTS_KEY = "ai_engine:agent_decision_memory:agents"


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


def _max_drawdown(daily_returns: list[float]) -> float:
    balance = peak = 1.0
    drawdown = 0.0
    for daily_return in daily_returns:
        balance *= max(0.0, 1.0 + daily_return / 100.0)
        peak = max(peak, balance)
        if peak:
            drawdown = max(drawdown, (peak - balance) / peak)
    return drawdown


def evaluate_promotion(observations: list[dict]) -> dict:
    """Evaluate one agent's time-ordered BUY overlay against its unmodified votes."""
    unique: dict[tuple[str, str, str], dict] = {}
    for sample in observations:
        key = (
            str(sample.get("symbol") or ""),
            str(sample.get("date") or ""),
            str(sample.get("candle_time") or ""),
        )
        if all(key):
            unique.setdefault(key, sample)

    samples = list(unique.values())
    dates = {str(sample["date"]) for sample in samples}
    kept = [sample for sample in samples if sample.get("shadow_action") != "HOLD"]
    baseline_wins = sum(float(sample["pnl_pct"]) > 0 for sample in samples)
    kept_wins = sum(float(sample["pnl_pct"]) > 0 for sample in kept)
    baseline_accuracy = baseline_wins / len(samples) if samples else 0.0
    overlay_accuracy = kept_wins / len(kept) if kept else 0.0

    daily_baseline: dict[str, list[float]] = {}
    daily_overlay: dict[str, list[float]] = {}
    for sample in samples:
        day = str(sample["date"])
        pnl = float(sample["pnl_pct"])
        daily_baseline.setdefault(day, []).append(pnl)
        daily_overlay.setdefault(day, []).append(
            0.0 if sample.get("shadow_action") == "HOLD" else pnl
        )

    baseline_returns = [
        sum(daily_baseline[day]) / len(daily_baseline[day])
        for day in sorted(daily_baseline)
    ]
    overlay_returns = [
        sum(daily_overlay[day]) / len(daily_overlay[day])
        for day in sorted(daily_overlay)
    ]
    baseline_mean = (
        sum(baseline_returns) / len(baseline_returns) if baseline_returns else 0.0
    )
    overlay_mean = (
        sum(overlay_returns) / len(overlay_returns) if overlay_returns else 0.0
    )
    baseline_drawdown = _max_drawdown(baseline_returns)
    overlay_drawdown = _max_drawdown(overlay_returns)
    improved = (
        len(samples) >= _MIN_PROMOTION_SAMPLES
        and len(dates) >= _MIN_PROMOTION_DATES
        and len(kept) >= _MIN_KEPT_SAMPLES
        and len(kept) < len(samples)
        and overlay_accuracy > baseline_accuracy
        and overlay_mean > baseline_mean
        and overlay_drawdown <= baseline_drawdown + 1e-12
    )
    return {
        "samples": len(samples),
        "dates": len(dates),
        "suppressed": len(samples) - len(kept),
        "kept": len(kept),
        "baseline_accuracy": round(baseline_accuracy, 4),
        "overlay_accuracy": round(overlay_accuracy, 4),
        "baseline_mean_daily_return_pct": round(baseline_mean, 6),
        "overlay_mean_daily_return_pct": round(overlay_mean, 6),
        "baseline_max_drawdown": round(baseline_drawdown, 6),
        "overlay_max_drawdown": round(overlay_drawdown, 6),
        "qualified": improved,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class AgentDecisionMemory:
    def __init__(self) -> None:
        self._case_cache: dict[str, tuple[float, list[dict]]] = {}
        self._refresh_lock = asyncio.Lock()

    @staticmethod
    def _default_enabled() -> bool:
        return os.getenv("AGENT_DECISION_MEMORY_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off",
        )

    async def _redis(self):
        from app.utils.redis_client import get_redis
        return get_redis()

    async def _load_agent_cases(self, redis, agents: list[str]) -> dict[str, list[dict]]:
        now = time.monotonic()
        missing = [
            name for name in agents
            if name not in self._case_cache
            or now - self._case_cache[name][0] >= _CACHE_TTL
        ]
        if missing:
            values = await redis.mget([_CASES_KEY.format(name) for name in missing])
            for name, value in zip(missing, values):
                decoded = value.decode("utf-8") if isinstance(value, bytes) else value
                cases = _json_value(decoded, [])
                self._case_cache[name] = (
                    now,
                    cases if isinstance(cases, list) else [],
                )
        return {name: self._case_cache[name][1] for name in agents}

    async def apply(
        self,
        signals: list[Any],
        fingerprint: list[float] | None,
        current_date: str | None,
    ) -> dict:
        """Score BUY votes from Redis and suppress only qualified agents' weak BUYs."""
        current_agents = [
            {"agent": signal.agent_name, "action": signal.action}
            for signal in signals
            if signal.action == "BUY"
        ]
        if not current_agents or not fingerprint or len(fingerprint) != FINGERPRINT_DIM:
            return {"mode": "shadow_only", "agents": []}

        redis = await self._redis()
        enabled_value = await redis.get(_ENABLED_KEY)
        enabled = self._default_enabled() if enabled_value is None else _truthy(enabled_value)
        if not enabled:
            return {"mode": "disabled", "agents": []}

        agent_names = list(dict.fromkeys(item["agent"] for item in current_agents))
        active_values = await redis.mget([_ACTIVE_KEY.format(name) for name in agent_names])
        active_by_agent: dict[str, bool] = {}
        for name, value in zip(agent_names, active_values):
            decoded = value.decode("utf-8") if isinstance(value, bytes) else value
            policy = _json_value(decoded, {})
            active_by_agent[name] = bool(
                isinstance(policy, dict) and policy.get("active") is True
            )

        cases_by_agent = await self._load_agent_cases(redis, agent_names)
        date = current_date or datetime.now(IST).strftime("%Y-%m-%d")
        query = np.asarray(fingerprint, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if not query_norm:
            return {"mode": "shadow_only", "agents": []}
        query /= query_norm

        similar: list[dict] = []
        for name in agent_names:
            cases = cases_by_agent[name]
            valid = []
            vectors = []
            for case in cases:
                vector = case.get("f")
                if (
                    not isinstance(vector, list)
                    or len(vector) != FINGERPRINT_DIM
                    or str(case.get("d") or "") >= date
                ):
                    continue
                try:
                    vectors.append([float(value) for value in vector])
                    valid.append(case)
                except (TypeError, ValueError):
                    continue
            if not vectors:
                continue
            matrix = np.asarray(vectors, dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1e-9
            scores = (matrix / norms) @ query
            positions = np.flatnonzero(scores >= _MIN_SIMILARITY)
            if len(positions) > _MAX_NEIGHBORS:
                positions = positions[
                    np.argpartition(scores[positions], -_MAX_NEIGHBORS)[-_MAX_NEIGHBORS:]
                ]
            for position in positions:
                case = valid[int(position)]
                similar.append({
                    "fingerprint": vectors[int(position)],
                    "pnl_pct": case["p"],
                    "mode": case.get("m", "replay"),
                    "agents": [{"agent": name, "action": "BUY"}],
                    "_similarity": float(scores[int(position)]),
                })

        report = build_shadow_report(current_agents, fingerprint, similar)
        report["mode"] = "shadow_only"
        report["enabled"] = True
        for item in report["agents"]:
            active = active_by_agent.get(item["agent"], False)
            item["active"] = active
            if active and item["shadow_action"] == "HOLD":
                item["applied"] = True
                for signal in signals:
                    if signal.agent_name == item["agent"] and signal.action == "BUY":
                        signal.indicators = dict(signal.indicators or {})
                        signal.indicators["decision_memory"] = {
                            **item,
                            "original_action": "BUY",
                            "actual_action": "HOLD",
                        }
                        signal.action = "HOLD"
                        signal.reasoning = (
                            f"{signal.reasoning} | decision memory suppressed weak BUY"
                        )
                        break
            else:
                item["applied"] = False
                for signal in signals:
                    if signal.agent_name == item["agent"] and signal.action == "BUY":
                        signal.indicators = dict(signal.indicators or {})
                        signal.indicators["decision_memory"] = {
                            **item,
                            "original_action": "BUY",
                            "actual_action": "BUY",
                        }
                        break

        if any(item.get("applied") for item in report["agents"]):
            report["mode"] = "active"
        return report

    async def _labeled_rows(self) -> list:
        from sqlalchemy import text
        from app.database.postgres import engine

        today = datetime.now(IST).strftime("%Y-%m-%d")
        async with engine.begin() as conn:
            return (await conn.execute(text("""
                SELECT d.id, d.symbol, d.candle_time, sm.date,
                       d.cf_pnl_pct, d.fingerprint, d.agents,
                       sm.mode, d.agent_memory_shadow
                FROM session_decisions d
                JOIN session_metadata sm ON sm.session_id = d.session_id
                WHERE d.fingerprint IS NOT NULL
                  AND d.cf_pnl_pct IS NOT NULL
                  AND d.executed = FALSE
                  AND sm.completed_at IS NOT NULL
                  AND sm.date < :today
                ORDER BY sm.date DESC, d.id DESC
                LIMIT :max_cases
            """), {"today": today, "max_cases": _MAX_CASES})).fetchall()

    async def refresh_and_promote(self) -> dict:
        """Refresh Redis shadow cases and automatically activate qualified agents."""
        async with self._refresh_lock:
            rows = await self._labeled_rows()
            redis = await self._redis()
            cases_by_agent: dict[str, list[dict]] = {}
            observations_by_agent: dict[str, dict[tuple[str, str, str], dict]] = {}

            for row in rows:
                symbol = str(row[1] or "").upper()
                candle_time = str(row[2] or "")
                day = str(row[3] or "")
                fingerprint = _json_value(row[5], None)
                signals = _json_value(row[6], [])
                shadow = _json_value(row[8], {})
                if not isinstance(fingerprint, list) or len(fingerprint) != FINGERPRINT_DIM:
                    continue
                if not isinstance(signals, list):
                    signals = []
                try:
                    vector = [round(float(value), 5) for value in fingerprint]
                    pnl_pct = float(row[4])
                except (TypeError, ValueError):
                    continue

                for signal in signals:
                    if not isinstance(signal, dict):
                        continue
                    name = str(signal.get("agent") or signal.get("agent_name") or "")
                    action = str(
                        signal.get("memory_original_action") or signal.get("action") or ""
                    ).upper()
                    if name and action == "BUY":
                        bucket = cases_by_agent.setdefault(name, [])
                        if len(bucket) < _MAX_CASES_PER_AGENT:
                            bucket.append({
                                "f": vector, "p": round(pnl_pct, 5),
                                "m": str(row[7] or "replay").lower(), "d": day,
                            })

                if not isinstance(shadow, dict) or not isinstance(shadow.get("agents"), list):
                    continue
                sample_key = (symbol, day, candle_time)
                for item in shadow["agents"]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("agent") or "")
                    if item.get("action") != "BUY" or not name:
                        continue
                    observations_by_agent.setdefault(name, {}).setdefault(
                        sample_key,
                        {
                            "symbol": symbol, "date": day, "candle_time": candle_time,
                            "pnl_pct": pnl_pct,
                            "shadow_action": str(item.get("shadow_action") or "BUY").upper(),
                        },
                    )

            known_agents = set(cases_by_agent) | set(observations_by_agent)
            known_agents.update(
                name.decode("utf-8") if isinstance(name, bytes) else str(name)
                for name in await redis.smembers(_AGENTS_KEY)
            )
            if known_agents:
                await redis.sadd(_AGENTS_KEY, *known_agents)

            policies = []
            for name in sorted(known_agents):
                cases = cases_by_agent.get(name, [])
                await redis.set(_CASES_KEY.format(name), json.dumps(cases, separators=(",", ":")))
                self._case_cache.pop(name, None)
                metrics = evaluate_promotion(
                    list(observations_by_agent.get(name, {}).values())
                )
                policy = {
                    "active": metrics["qualified"],
                    "updated_at": datetime.now(IST).isoformat(),
                    "metrics": metrics,
                }
                await redis.set(_ACTIVE_KEY.format(name), json.dumps(policy, separators=(",", ":")))
                policies.append({"agent": name, **policy})

            return {
                "rows": len(rows),
                "agents": len(known_agents),
                "promoted": sum(policy["active"] for policy in policies),
                "evaluated": policies,
            }

    async def set_enabled(self, enabled: bool) -> dict:
        redis = await self._redis()
        await redis.set(_ENABLED_KEY, "1" if enabled else "0")
        return {"enabled": enabled}

    async def status(self) -> dict:
        from sqlalchemy import text
        from app.database.postgres import engine

        redis = await self._redis()
        enabled_value = await redis.get(_ENABLED_KEY)
        enabled = self._default_enabled() if enabled_value is None else _truthy(enabled_value)
        names = [
            name.decode("utf-8") if isinstance(name, bytes) else str(name)
            for name in await redis.smembers(_AGENTS_KEY)
        ]
        values = await redis.mget([_ACTIVE_KEY.format(name) for name in names])
        active = {}
        for name, value in zip(names, values):
            decoded = value.decode("utf-8") if isinstance(value, bytes) else value
            active[name] = _json_value(decoded, {})
        case_values = await redis.mget([_CASES_KEY.format(name) for name in names])
        staged_counts = {}
        for name, value in zip(names, case_values):
            decoded = value.decode("utf-8") if isinstance(value, bytes) else value
            cases = _json_value(decoded, [])
            staged_counts[name] = len(cases) if isinstance(cases, list) else 0

        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT
                    signal->>'agent' AS agent,
                    COUNT(DISTINCT (
                        d.symbol,
                        COALESCE(sm.date, ''),
                        d.candle_time
                    ))::int AS samples,
                    COUNT(DISTINCT sm.date)::int AS dates
                FROM session_decisions d
                JOIN session_metadata sm ON sm.session_id = d.session_id
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(d.agent_memory_shadow->'agents', '[]'::jsonb)
                ) AS signal
                WHERE d.cf_pnl_pct IS NOT NULL
                  AND d.executed = FALSE
                  AND signal->>'action' = 'BUY'
                  AND d.agent_memory_shadow IS NOT NULL
                GROUP BY signal->>'agent'
                ORDER BY signal->>'agent'
            """))).fetchall()

        sample_counts = {
            row[0]: {"shadow_samples": int(row[1]), "shadow_dates": int(row[2])}
            for row in rows
        }
        evaluated = []
        for name in sorted(set(names) | set(sample_counts)):
            policy = active.get(name, {})
            evaluated.append({
                "agent": name,
                "active": bool(policy.get("active")),
                "staged_buy_cases": staged_counts.get(name, 0),
                **sample_counts.get(name, {"shadow_samples": 0, "shadow_dates": 0}),
                **policy.get("metrics", {}),
            })

        return {
            "mode": "redis_promoted",
            "enabled": enabled,
            "promotion_gate": {
                "min_resolved_examples": _MIN_PROMOTION_SAMPLES,
                "min_distinct_dates": _MIN_PROMOTION_DATES,
                "conditions": [
                    "higher held-out BUY accuracy",
                    "higher mean daily return after costs",
                    "no increase in maximum drawdown",
                ],
            },
            "evaluated": evaluated,
        }


_memory: AgentDecisionMemory | None = None


def get_agent_decision_memory() -> AgentDecisionMemory:
    global _memory
    if _memory is None:
        _memory = AgentDecisionMemory()
    return _memory
