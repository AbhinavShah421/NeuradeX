import pytest

import app.services.sessions_service as sessions_service
from app.services.sessions_service import _normalise_speed
from app.services.backtest_service import _fetch_full_day_candles


@pytest.mark.parametrize("speed", [1, 2, 5, 10, 30, 60, 120])
def test_session_speed_accepts_autopilot_range(speed):
    assert _normalise_speed(speed) == speed


@pytest.mark.parametrize("speed", [0, -1, 121])
def test_session_speed_defaults_out_of_range_values(speed):
    assert _normalise_speed(speed) == 1


@pytest.mark.asyncio
async def test_replay_advances_speed_bars_per_tick(monkeypatch):
    advanced = []

    async def fake_step(session, window, force_close):
        advanced.append((len(window), force_close))

    monkeypatch.setattr(sessions_service, "_step", fake_step)
    session = {
        "all_candles": [{"time": f"09:{minute:02d}"} for minute in range(30)],
        "current_idx": 0,
        "speed": 10,
        "status": "running",
    }

    await sessions_service._advance_replay(session)

    assert session["current_idx"] == 10
    assert session["status"] == "running"
    assert advanced == [(length, False) for length in range(2, 12)]


@pytest.mark.asyncio
async def test_full_day_provider_fallback_requests_one_minute_bars(monkeypatch):
    import app.data.candle_store as candle_store
    import app.data.providers as providers

    monkeypatch.setattr(candle_store, "read_bars", lambda *_args: [])
    requested = []

    async def fake_fetch_intraday(symbol, date, interval):
        requested.append(interval)
        return ([{"time": "10:00", "close": 100.0}] * 30, "provider")

    monkeypatch.setattr(providers, "fetch_intraday", fake_fetch_intraday)
    candles, source = await _fetch_full_day_candles("TEST", "2026-10-16")

    assert requested == [1]
    assert len(candles) == 30
    assert source == "provider"


@pytest.mark.asyncio
async def test_change_speed_updates_running_session(monkeypatch):
    session = {
        "id": "session-1", "mode": "backtest", "symbol": "TEST",
        "status": "running", "speed": 1, "position": {}, "metrics": {},
        "trades": [], "agent_decision": {},
    }
    saved = []

    async def get_session(_session_id):
        return session

    async def save_session(updated):
        saved.append(updated["speed"])

    monkeypatch.setattr(sessions_service, "get_session", get_session)
    monkeypatch.setattr(sessions_service, "save_session", save_session)

    result = await sessions_service.set_speed(
        "session-1", sessions_service.SpeedRequest(speed=60))

    assert session["speed"] == 60
    assert saved == [60]
    assert result["data"]["speed"] == 60