"""Tests for the per-agent decision-memory shadow evaluator."""

import asyncio
import json

from app.agents.agent_decision_memory import (
    AgentDecisionMemory,
    _ACTIVE_KEY,
    _CASES_KEY,
    _ENABLED_KEY,
    build_shadow_report,
    evaluate_promotion,
)
from app.agents.base import AgentSignal
from app.agents.fingerprint import FINGERPRINT_DIM


_FINGERPRINT = [1.0] + [0.0] * (FINGERPRINT_DIM - 1)


def _cases(action: str, pnl_values: list[float], mode: str = "paper") -> list[dict]:
    return [
        {
            "fingerprint": _FINGERPRINT,
            "pnl_pct": pnl,
            "mode": mode,
            "agents": [{"agent": "technical", "action": action}],
        }
        for pnl in pnl_values
    ]


def test_poor_buy_history_only_suggests_hold_in_shadow():
    report = build_shadow_report(
        [{"agent": "technical", "action": "BUY"}],
        _FINGERPRINT,
        _cases("BUY", [-0.2] * 8),
    )

    evidence = report["agents"][0]
    assert report["mode"] == "shadow_only"
    assert evidence["assessment"] == "caution"
    assert evidence["shadow_action"] == "HOLD"
    assert evidence["historical_accuracy"] == 0.0


def test_strong_history_supports_signal_without_changing_it():
    report = build_shadow_report(
        [{"agent": "technical", "action": "BUY"}],
        _FINGERPRINT,
        _cases("BUY", [0.2] * 8),
    )

    evidence = report["agents"][0]
    assert evidence["assessment"] == "support"
    assert evidence["shadow_action"] == "BUY"


def test_replay_heavy_history_does_not_meet_effective_sample_floor():
    report = build_shadow_report(
        [{"agent": "technical", "action": "BUY"}],
        _FINGERPRINT,
        _cases("BUY", [-0.2] * 8, mode="replay"),
    )

    evidence = report["agents"][0]
    assert evidence["assessment"] == "insufficient_evidence"
    assert evidence["shadow_action"] == "BUY"
    assert evidence["effective_neighbors"] == 2.4


def test_unrelated_patterns_are_not_used_as_evidence():
    unrelated = [0.0, 1.0] + [0.0] * (FINGERPRINT_DIM - 2)
    cases = _cases("BUY", [-0.2] * 8)
    for case in cases:
        case["fingerprint"] = unrelated

    report = build_shadow_report(
        [{"agent": "technical", "action": "BUY"}],
        _FINGERPRINT,
        cases,
    )

    assert report["agents"][0]["assessment"] == "insufficient_evidence"
    assert report["agents"][0]["neighbors"] == 0


def test_memory_is_agent_and_action_specific():
    cases = _cases("BUY", [-0.2] * 8)
    report = build_shadow_report(
        [{"agent": "pattern", "action": "BUY"}],
        _FINGERPRINT,
        cases,
    )

    assert report["agents"][0]["assessment"] == "insufficient_evidence"


def _promotion_samples(
    suppressed_pnl: list[float],
    kept_pnl: list[float],
) -> list[dict]:
    samples = []
    pnls = [(pnl, True) for pnl in suppressed_pnl]
    pnls.extend((pnl, False) for pnl in kept_pnl)
    for index, (pnl, suppress) in enumerate(pnls):
        samples.append({
            "symbol": f"SYM{index % 5}",
            "date": f"2026-01-{index // 10 + 1:02d}",
            "candle_time": f"09:{index % 10:02d}",
            "pnl_pct": pnl,
            "shadow_action": "HOLD" if suppress else "BUY",
        })
    return samples


def test_promotion_requires_sample_and_date_minimums():
    samples = _promotion_samples([-0.5] * 90, [0.4] * 90)

    metrics = evaluate_promotion(samples)

    assert metrics["samples"] == 180
    assert metrics["dates"] == 18
    assert metrics["qualified"] is False


def test_promotion_requires_improved_held_out_accuracy_and_return():
    samples = _promotion_samples([-0.5] * 100, [0.4] * 100)

    metrics = evaluate_promotion(samples)

    assert metrics["samples"] == 200
    assert metrics["dates"] == 20
    assert metrics["overlay_accuracy"] > metrics["baseline_accuracy"]
    assert (
        metrics["overlay_mean_daily_return_pct"]
        > metrics["baseline_mean_daily_return_pct"]
    )
    assert metrics["overlay_max_drawdown"] <= metrics["baseline_max_drawdown"]
    assert metrics["qualified"] is True


def test_promotion_rejects_accuracy_gain_that_lowers_return():
    samples = _promotion_samples(
        [-1.0] * 90 + [100.0] * 10,
        [1.0] * 90 + [-1.0] * 10,
    )

    metrics = evaluate_promotion(samples)

    assert metrics["overlay_accuracy"] > metrics["baseline_accuracy"]
    assert (
        metrics["overlay_mean_daily_return_pct"]
        < metrics["baseline_mean_daily_return_pct"]
    )
    assert metrics["qualified"] is False


def test_redis_memory_suppresses_qualified_buy_and_kill_switch_restores_it():
    class FakeRedis:
        enabled = b"1"

        async def get(self, key):
            return self.enabled if key == _ENABLED_KEY else None

        async def mget(self, keys):
            if keys == [_ACTIVE_KEY.format("technical")]:
                return [b'{"active":true}']
            if keys == [_CASES_KEY.format("technical")]:
                return [json.dumps([
                    {
                        "f": _FINGERPRINT,
                        "p": -0.5,
                        "m": "paper",
                        "d": "2026-02-01",
                    }
                    for _ in range(8)
                ])]
            return [None for _ in keys]

    memory = AgentDecisionMemory()
    redis = FakeRedis()

    async def get_redis():
        return redis

    memory._redis = get_redis
    signal = AgentSignal("technical", "BUY", 0.8, "original")

    report = asyncio.run(memory.apply([signal], _FINGERPRINT, "2026-03-01"))

    assert report["mode"] == "active"
    assert report["agents"][0]["applied"] is True
    assert signal.action == "HOLD"

    redis.enabled = b"0"
    signal = AgentSignal("technical", "BUY", 0.8, "original")
    report = asyncio.run(memory.apply([signal], _FINGERPRINT, "2026-03-01"))

    assert report["mode"] == "disabled"
    assert signal.action == "BUY"
