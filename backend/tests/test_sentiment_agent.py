import importlib.util
import logging
import sys
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


def _load_sentiment_agent():
    agents_package = ModuleType("app.agents")
    agents_package.__path__ = [str(Path(__file__).parents[1] / "app" / "agents")]
    base_module = ModuleType("app.agents.base")

    @dataclass
    class AgentSignal:
        agent_name: str
        action: str
        confidence: float
        reasoning: str
        indicators: dict = field(default_factory=dict)
        weight: float = 1.0

    class BaseAgent(ABC):
        pass

    base_module.AgentSignal = AgentSignal
    base_module.BaseAgent = BaseAgent
    logger_module = ModuleType("app.utils.elk_logger")
    logger_module.get_logger = logging.getLogger
    previous = {
        name: sys.modules.get(name)
        for name in ("app.agents", "app.agents.base", "app.utils.elk_logger")
    }
    sys.modules["app.agents"] = agents_package
    sys.modules["app.agents.base"] = base_module
    sys.modules["app.utils.elk_logger"] = logger_module
    try:
        path = Path(__file__).parents[1] / "app" / "agents" / "sentiment.py"
        spec = importlib.util.spec_from_file_location("app.agents.sentiment", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


sentiment_agent = _load_sentiment_agent()
_score_matches_sentiment = sentiment_agent._score_matches_sentiment
_sentiment_payload_is_fresh = sentiment_agent._sentiment_payload_is_fresh


def test_sentiment_score_sign_must_match_label():
    assert _score_matches_sentiment("positive", 0.8)
    assert _score_matches_sentiment("negative", -0.8)
    assert not _score_matches_sentiment("positive", -0.8)
    assert not _score_matches_sentiment("negative", 0.8)


def test_sentiment_score_must_be_finite_and_in_range():
    assert not _score_matches_sentiment("positive", 0.0)
    assert not _score_matches_sentiment("positive", 1.1)
    assert not _score_matches_sentiment("negative", float("inf"))
    assert not _score_matches_sentiment("positive", float("nan"))


def test_live_sentiment_requires_recent_epoch_or_iso_timestamp():
    now = 1_800_000_000.0
    assert _sentiment_payload_is_fresh({"fetched_at": now - 60}, now=now)
    assert _sentiment_payload_is_fresh(
        {"updated_at": "2027-01-15T08:00:00+00:00"}, now=1_800_000_000.0
    )
    assert not _sentiment_payload_is_fresh({"fetched_at": now - 5401}, now=now)
    assert not _sentiment_payload_is_fresh({"fetched_at": "invalid"}, now=now)
    assert not _sentiment_payload_is_fresh({}, now=now)


def test_historical_sentiment_must_match_requested_date():
    assert _sentiment_payload_is_fresh({"date": "2026-10-15"}, date="2026-10-15")
    assert not _sentiment_payload_is_fresh({"date": "2026-10-14"}, date="2026-10-15")