"""Days with no live tick capture are labelled from provider bars — near-complete days only."""
import asyncio

from app.agents import counterfactual as cf


def _bars(n):
    return [{"time": f"{9 + (15 + i) // 60:02d}:{(15 + i) % 60:02d}", "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": 1} for i in range(n)]


def _patch(monkeypatch, candles, source):
    import app.data.providers as providers

    async def fake(symbol, day, interval):
        assert interval == 1
        return candles, source
    monkeypatch.setattr(providers, "fetch_intraday", fake)


def test_full_provider_day_is_used(monkeypatch):
    _patch(monkeypatch, _bars(360), "yahoo")
    assert len(asyncio.run(cf._provider_bars("ABC", "2026-11-20"))) == 360


def test_partial_provider_day_is_refused(monkeypatch):
    _patch(monkeypatch, _bars(120), "yahoo")
    assert asyncio.run(cf._provider_bars("ABC", "2026-11-20")) == []


def test_simulated_bars_are_refused(monkeypatch):
    _patch(monkeypatch, _bars(375), "simulated")
    assert asyncio.run(cf._provider_bars("ABC", "2026-11-20")) == []


def test_provider_error_is_empty(monkeypatch):
    import app.data.providers as providers

    async def boom(*a):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(providers, "fetch_intraday", boom)
    assert asyncio.run(cf._provider_bars("ABC", "2026-11-20")) == []
