"""The executor chain is fed only by entries a paper session actually opens.

2026-09-15: EnsembleEngine.decide() published every call, before any gate — 537
raw decisions from 6 symbols in one day. The executor bought 8 times on stocks
falling 3-8%, and at each of those exact minutes the session gate had refused the
same symbol as a counter-trend falling knife. 6 of 7 closed trades hit their stop.
"""
import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from app.agents.ensemble import EnsembleEngine
from app.services import sessions_service as ss
from app.utils import decision_publisher as dp


def _decision(action="HOLD", confidence=0.66, agreement=0.5):
    vote = SimpleNamespace(agent_name="gbm", action="BUY", confidence=0.74, weight=1.7)
    return SimpleNamespace(action=action, confidence=confidence,
                           agent_agreement=agreement, agents=[vote])


@pytest.fixture
def captured(monkeypatch):
    """Record what would go to RabbitMQ, built by the real payload mapper."""
    sent = []

    async def fake_publish(decision, symbol, context=None):
        sent.append(dp.build_payload(decision, symbol, context))
        return True

    monkeypatch.setattr(dp, "publish_decision", fake_publish)
    return sent


def run(coro):
    return asyncio.run(coro)


def test_decide_no_longer_publishes():
    # Publishing from decide() is what sent ungated signals, and it fired for any
    # caller without an explicit mode: scanner, AI Engine analyze, backtest training.
    assert "publish_decision" not in inspect.getsource(EnsembleEngine.decide)


def test_a_paper_entry_is_published_as_a_buy_at_the_sessions_price(captured):
    # The ensemble's own action can be HOLD while the gate enters on score; the
    # message must still say BUY or risk-engine skips it.
    ok = run(ss._publish_entry_to_executor(
        {"mode": "paper"}, "RELIANCE", _decision(action="HOLD", confidence=0.66),
        {"close": 1401.5}, {"atr": 3.2}))

    assert ok is True
    assert len(captured) == 1
    p = captured[0]
    assert p["finalAction"] == "BUY" and p["symbol"] == "RELIANCE"
    assert p["currentPrice"] == 1401.5 and p["atr"] == 3.2
    assert p["weightedConfidence"] == 0.66, "confidence stays the ensemble's real number"
    assert p["agentVotes"]["gbm"]["signal"] == "BUY"


@pytest.mark.parametrize("mode", ["replay", "backtest", "", None])
def test_only_paper_sessions_publish(captured, mode):
    # Replay and backtest bars are history; sending them to a live risk engine
    # would place orders off the past.
    run(ss._publish_entry_to_executor({"mode": mode}, "RELIANCE", _decision(),
                                      {"close": 1.0}, {}))
    assert captured == []


def test_no_ensemble_decision_publishes_nothing(captured):
    assert run(ss._publish_entry_to_executor(
        {"mode": "paper"}, "RELIANCE", None, {"close": 1.0}, {})) is False
    assert captured == []


def test_a_broker_failure_never_breaks_the_session(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("rabbit down")

    monkeypatch.setattr(dp, "publish_decision", boom)
    assert run(ss._publish_entry_to_executor(
        {"mode": "paper"}, "RELIANCE", _decision(), {"close": 1.0}, {})) is False


def test_step_publishes_only_from_the_block_that_opens_the_position():
    # The single publish point must sit where the session really enters — after
    # the late-entry cutoff, the daily loss breaker and the cash check — and
    # nowhere earlier. An earlier post-gate hook lived in a helper the live path
    # never called and published nothing for weeks.
    src = inspect.getsource(ss._step)
    assert src.count("_publish_entry_to_executor(") == 1, "exactly one publish point"
    entry_block = src.index('if action == "BUY" and pos["status"] == "NONE":')
    position_opened = src.index('s["position"] = {', entry_block)
    assert src.index("_publish_entry_to_executor(") > position_opened


# ── The hook must be observable, not only on a rare paper entry ─────────────
# Added 2026-09-16. The call site was wrapped in a paper-only `if`, so nothing
# proved it ran until a gated paper BUY happened — and none had since deploy.
# Replay and backtest entries go through the same block, so routing them through
# the helper (which refuses to publish them) is what makes the call site testable
# in production.

def test_the_call_site_is_not_gated_on_paper_mode():
    src = inspect.getsource(ss._step)
    i = src.index("_publish_entry_to_executor(")
    assert 'if s.get("mode") == "paper"' not in src[max(0, i - 400):i], (
        "call the helper for every mode; its own guard stops non-paper publishing, "
        "and replay/backtest entries are what prove this call site runs"
    )


def test_a_paper_publish_is_recorded_for_later_inspection(monkeypatch, captured):
    beats = []

    async def fake_set(key, value, expire=None):
        beats.append((key, json.loads(value)))

    monkeypatch.setattr("app.utils.redis_client.cache_set", fake_set)
    run(ss._publish_entry_to_executor({"mode": "paper"}, "RELIANCE", _decision(),
                                      {"close": 10.0, "time": "09:45"}, {"atr": 1.0}))

    assert len(beats) == 1
    key, beat = beats[0]
    assert key == ss._ENTRY_HOOK_KEY
    assert beat["published"] is True
    assert beat["symbol"] == "RELIANCE" and beat["mode"] == "paper"
    assert beat["candle_time"] == "09:45" and beat["price"] == 10.0


def test_a_replay_entry_still_records_that_the_hook_ran(monkeypatch, captured):
    beats = []

    async def fake_set(key, value, expire=None):
        beats.append(json.loads(value))

    monkeypatch.setattr("app.utils.redis_client.cache_set", fake_set)
    run(ss._publish_entry_to_executor({"mode": "replay"}, "SBIN", _decision(),
                                      {"close": 10.0, "time": "09:45"}, {}))

    assert captured == [], "replay bars must never reach a live risk engine"
    assert beats and beats[0]["published"] is False
    assert "not paper" in beats[0]["why"]


def test_a_heartbeat_failure_never_breaks_the_session(monkeypatch, captured):
    async def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.utils.redis_client.cache_set", boom)
    assert run(ss._publish_entry_to_executor(
        {"mode": "paper"}, "RELIANCE", _decision(),
        {"close": 10.0, "time": "09:45"}, {"atr": 1.0})) is True
