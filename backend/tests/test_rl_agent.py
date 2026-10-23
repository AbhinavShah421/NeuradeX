from __future__ import annotations

import pytest

from app.agents.rl_agent import N_STATES, RLAgent


def _candles() -> list[dict]:
    return [
        {"close": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "volume": 100}
        for i in range(30)
    ]


def _agent_with_buy_policy(candles: list[dict]) -> RLAgent:
    agent = RLAgent()
    agent._q = [[0.0, 0.0, 0.0] for _ in range(N_STATES)]
    agent._q[agent.extract_state(candles)] = [1.0, 0.0, 0.0]
    return agent


@pytest.mark.asyncio
async def test_replay_uses_learned_policy_without_random_exploration(monkeypatch):
    candles = _candles()
    agent = _agent_with_buy_policy(candles)

    def unexpected_random_call():
        pytest.fail("ordinary replay should not consult the random generator")

    monkeypatch.setattr("app.agents.rl_agent.random.random", unexpected_random_call)
    signal = await agent.analyze("TEST", candles, {"mode": "replay"})

    assert signal.action == "BUY"
    assert signal.indicators["source"] == "exploit"


@pytest.mark.asyncio
async def test_replay_exploration_requires_explicit_opt_in(monkeypatch):
    candles = _candles()
    agent = _agent_with_buy_policy(candles)
    monkeypatch.setattr("app.agents.rl_agent.random.random", lambda: 0.0)
    monkeypatch.setattr("app.agents.rl_agent.random.randint", lambda _low, _high: 1)

    signal = await agent.analyze("TEST", candles, {"mode": "replay", "explore": True})

    assert signal.action == "SELL"
    assert signal.indicators["source"] == "explore"