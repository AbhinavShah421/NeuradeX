import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_fundamentals_analyst():
    config_module = ModuleType("app.config")
    config_module.settings = SimpleNamespace(ALPHA_VANTAGE_KEY="")
    redis_module = ModuleType("app.utils.redis_client")

    async def cache_get(_key):
        return None

    async def cache_set(*_args, **_kwargs):
        return None

    redis_module.cache_get = cache_get
    redis_module.cache_set = cache_set
    previous = {
        name: sys.modules.get(name)
        for name in ("app.config", "app.utils.redis_client")
    }
    sys.modules["app.config"] = config_module
    sys.modules["app.utils.redis_client"] = redis_module
    try:
        path = Path(__file__).parents[1] / "app" / "agents" / "fundamentals_analyst.py"
        spec = importlib.util.spec_from_file_location("fundamentals_analyst_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


fundamentals = _load_fundamentals_analyst()


def test_normalize_overview_parses_finite_metrics_and_provenance():
    report = fundamentals.normalize_overview({
        "Symbol": "RELIANCE.BSE",
        "Name": "Reliance Industries",
        "Sector": "Energy",
        "Currency": "INR",
        "PERatio": "24.5",
        "ProfitMargin": "0.12",
        "DebtToEquity": "None",
        "EPS": "NaN",
    })

    assert report["symbol"] == "RELIANCE.BSE"
    assert report["metrics"]["pe_ratio"] == 24.5
    assert report["metrics"]["profit_margin"] == 0.12
    assert report["metrics"]["debt_to_equity"] is None
    assert report["metrics"]["eps"] is None
    assert report["provider"] == "alphavantage"
    assert report["status"] == "available"


def test_normalize_overview_rejects_error_and_empty_payloads():
    assert fundamentals.normalize_overview({"Note": "rate limit"}) is None
    assert fundamentals.normalize_overview({"Symbol": "ABC"}) is None


def test_provider_symbol_uses_bse_suffix():
    assert fundamentals._provider_symbol("RELIANCE.NS") == "RELIANCE.BSE"


def test_missing_key_abstains_without_network():
    assert asyncio.run(fundamentals.get_fundamentals_report("RELIANCE")) is None