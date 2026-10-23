import json
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_research_debate():
    # app.agents.__init__ eagerly imports the entire ML roster; the reviewer
    # logic only needs two settings and an LLM function, which tests can mock.
    agents_package = ModuleType("app.agents")
    agents_package.__path__ = [str(Path(__file__).parents[1] / "app" / "agents")]
    fundamentals_module = ModuleType("app.agents.fundamentals_analyst")

    async def no_fundamentals(symbol):
        return None

    fundamentals_module.get_fundamentals_report = no_fundamentals
    llm_module = ModuleType("app.utils.llm_client")
    llm_module.llm_chat = None
    config_module = ModuleType("app.config")
    config_module.settings = SimpleNamespace(
        TRADINGAGENTS_SHADOW_REVIEW_ENABLED=False,
        TRADINGAGENTS_SHADOW_REVIEW_TIMEOUT=8.0,
    )
    previous = {
        name: sys.modules.get(name)
        for name in (
            "app.agents",
            "app.agents.fundamentals_analyst",
            "app.config",
            "app.utils.llm_client",
        )
    }
    sys.modules["app.agents"] = agents_package
    sys.modules["app.agents.fundamentals_analyst"] = fundamentals_module
    sys.modules["app.config"] = config_module
    sys.modules["app.utils.llm_client"] = llm_module
    try:
        path = Path(__file__).parents[1] / "app" / "agents" / "research_debate.py"
        spec = importlib.util.spec_from_file_location("app.agents.research_debate", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


research_debate = _load_research_debate()


def _signal(agent_name, action, confidence, reasoning):
    return SimpleNamespace(
        agent_name=agent_name,
        action=action,
        confidence=confidence,
        reasoning=reasoning,
    )


def test_extract_review_validates_and_normalizes_output():
    review = research_debate._extract_review(json.dumps({
        "bull_case": "Higher highs",
        "bear_case": "Weak volume",
        "trader_action": "buy",
        "trader_confidence": 0.73542,
        "risk_notes": ["Resistance nearby", 5],
    }))

    assert review == {
        "bull_case": "Higher highs",
        "bear_case": "Weak volume",
        "trader_action": "BUY",
        "trader_confidence": 0.735,
        "risk_notes": ["Resistance nearby"],
        "status": "shadow",
    }


@pytest.mark.parametrize("raw", [
    None,
    "not json",
    '{"trader_action":"BUY","trader_confidence":1.5}',
    '{"trader_action":"SHORT","trader_confidence":0.8}',
    '{"trader_action":"SELL","trader_confidence":true}',
])
def test_extract_review_rejects_invalid_output(raw):
    assert research_debate._extract_review(raw) is None


@pytest.mark.asyncio
async def test_review_is_disabled_by_default(monkeypatch):
    async def unexpected_call(*args, **kwargs):
        raise AssertionError("LLM must not run when shadow review is disabled")

    monkeypatch.setattr(research_debate.settings, "TRADINGAGENTS_SHADOW_REVIEW_ENABLED", False)
    monkeypatch.setattr(research_debate, "llm_chat", unexpected_call)

    result = await research_debate.review_signals("ABC", [
        _signal("technical", "BUY", 0.7, "Uptrend"),
    ])

    assert result is None


@pytest.mark.asyncio
async def test_review_uses_reports_and_returns_shadow_result(monkeypatch):
    calls = []

    async def fake_llm(prompt, **kwargs):
        assert kwargs["temperature"] == 0.0
        calls.append(prompt)
        if "Bull Researcher" in prompt:
            assert '"analyst": "technical"' in prompt
            return json.dumps({"argument": "Trend is rising", "evidence_used": ["Uptrend"], "key_risks": []})
        if "Bear Researcher" in prompt:
            assert '"analyst": "technical"' in prompt
            return json.dumps({"argument": "Volume is thin", "evidence_used": [], "key_risks": ["Thin volume"]})
        assert "Trend is rising" in prompt and "Volume is thin" in prompt
        return json.dumps({
            "trader_action": "HOLD",
            "trader_confidence": 0.61,
            "rationale": "Evidence is mixed",
            "strategic_actions": ["Wait for confirmation"],
            "risk_notes": [],
        })

    monkeypatch.setattr(research_debate.settings, "TRADINGAGENTS_SHADOW_REVIEW_ENABLED", True)
    monkeypatch.setattr(research_debate, "llm_chat", fake_llm)

    result = await research_debate.review_signals("ABC", [
        _signal("technical", "BUY", 0.7, "Uptrend"),
        _signal("anomaly", "HOLD", 0.9, "Anomaly marker"),
    ])

    assert result["trader_action"] == "HOLD"
    assert result["status"] == "shadow"
    assert result["researchers"]["bull"]["argument"] == "Trend is rising"
    assert result["researchers"]["bear"]["argument"] == "Volume is thin"
    assert result["research_manager"]["rationale"] == "Evidence is mixed"
    assert len(calls) == 3