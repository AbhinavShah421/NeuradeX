"""Tests for the per-agent decision-memory shadow evaluator."""

from app.agents.agent_decision_memory import build_shadow_report
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
