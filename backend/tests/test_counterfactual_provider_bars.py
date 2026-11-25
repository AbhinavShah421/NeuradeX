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


def test_thin_but_full_day_is_used(monkeypatch):
    # a quiet small cap: 264 bars with gaps, but 09:15 -> 15:29 (OMPOWER 2026-11-23)
    bars = _bars(375)[::1]
    thin = [b for i, b in enumerate(bars) if i % 10 not in (1, 4, 7)]   # drop ~30% of minutes
    assert 200 <= len(thin) < 300 and thin[0]["time"] == "09:15" and thin[-1]["time"] >= "15:15"
    _patch(monkeypatch, thin, "yahoo")
    assert len(asyncio.run(cf._provider_bars("ABC", "2026-11-20"))) == len(thin)


def test_thin_day_that_stops_early_is_refused(monkeypatch):
    thin = [b for i, b in enumerate(_bars(375)) if i % 10 not in (1, 4, 7)]
    cut = [b for b in thin if b["time"] < "14:00"]
    _patch(monkeypatch, cut, "yahoo")
    assert asyncio.run(cf._provider_bars("ABC", "2026-11-20")) == []
