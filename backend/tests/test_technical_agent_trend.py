"""Technical agent: trend terms (VWAP / MACD / SMA) are reported but not scored by default."""
import asyncio
import importlib


def _candles(closes):
    return [{"time": f"10:{i:02d}", "open": c, "high": c * 1.001, "low": c * 0.999,
             "close": c, "volume": 1000} for i, c in enumerate(closes)]


def _agent(monkeypatch, trend):
    if trend is None:
        monkeypatch.delenv("NEURADEX_TECH_TREND", raising=False)
    else:
        monkeypatch.setenv("NEURADEX_TECH_TREND", trend)
    import app.agents.technical as tech
    tech = importlib.reload(tech)
    return tech.TechnicalAgent()


# A slow, steady climb: above VWAP, MACD rising/bullish, SMA5 > SMA20, RSI high.
_RISING = [100 + 0.05 * i for i in range(60)]


def test_trend_terms_not_scored_by_default(monkeypatch):
    sig = asyncio.run(_agent(monkeypatch, None).analyze("ABC", _candles(_RISING), {}))
    assert sig.action != "BUY"
    assert "above VWAP" in sig.reasoning          # still computed and reported


def test_legacy_switch_restores_trend_terms(monkeypatch):
    default = asyncio.run(_agent(monkeypatch, None).analyze("ABC", _candles(_RISING), {}))
    legacy = asyncio.run(_agent(monkeypatch, "1").analyze("ABC", _candles(_RISING), {}))
    # Trend terms push a rising stock's score up when scored.
    assert legacy.indicators["score"] > default.indicators["score"]


def test_oversold_dip_still_buys(monkeypatch):
    dip = [100.0] * 40 + [100 - 0.4 * i for i in range(1, 21)]
    sig = asyncio.run(_agent(monkeypatch, None).analyze("ABC", _candles(dip), {}))
    assert sig.action == "BUY"
