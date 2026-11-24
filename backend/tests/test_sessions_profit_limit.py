import asyncio

import pytest
from fastapi import HTTPException

from app.services import sessions_service


def test_paper_session_start_is_rejected_when_daily_limit_is_halted(monkeypatch):
    async def halt_active():
        return True

    monkeypatch.setattr(sessions_service, "_paper_profit_halt_active", halt_active)
    request = sessions_service.StartSessionRequest(mode="paper", symbol="SBIN")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(sessions_service.start_session(request))

    assert exc_info.value.status_code == 409
    assert "paused until the next IST day" in exc_info.value.detail


def test_profit_limit_stops_session_and_persists_it(monkeypatch):
    session = {"status": "running", "updated_at": "old"}
    finalized = []
    saved = []

    async def finalize(value):
        finalized.append(value.copy())

    async def save(value):
        saved.append(value.copy())

    monkeypatch.setattr(sessions_service, "_finalize_session", finalize)
    monkeypatch.setattr(sessions_service, "save_session", save)

    asyncio.run(sessions_service._stop_paper_session_at_profit_limit(session))

    assert session["status"] == "stopped"
    assert "Daily paper-trading profit limit reached" in session["error"]
    assert finalized == [session]
    assert saved == [session]
