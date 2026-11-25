import asyncio
import json
from datetime import datetime

from app import autopilot


class FakeRedis:
    def __init__(self, value):
        self.value = value

    async def get(self, key):
        return self.value


def _set(monkeypatch, items):
    payload = {"updated_at": "2026-11-25T09:20:00+05:30", "items": items}
    async def get_redis():
        return FakeRedis(json.dumps(payload))
    monkeypatch.setattr(autopilot, "_get_redis", get_redis)
    monkeypatch.setattr(autopilot, "_now_ist",
                        lambda: datetime(2026, 11, 25, 9, 25, tzinfo=autopilot.IST))


def test_low_gap_keeps_flat_openers_most_volatile_first(monkeypatch):
    _set(monkeypatch, [
        {"symbol": "GAPUP", "change_pct": 6.0, "atr_pct": 5.0},     # big gainer: excluded
        {"symbol": "GAPDN", "change_pct": -3.0, "atr_pct": 4.0},    # big gap-down: excluded
        {"symbol": "FLATA", "change_pct": 0.2, "atr_pct": 1.5},
        {"symbol": "FLATB", "change_pct": -0.4, "atr_pct": 3.1},
        {"symbol": "FLATC", "change_pct": 0.0},                     # no atr: last
    ])
    assert asyncio.run(autopilot._low_gap_symbols(10)) == ["FLATB", "FLATA", "FLATC"]


def test_selection_switch(monkeypatch):
    _set(monkeypatch, [{"symbol": "GAPUP", "change_pct": 6.0, "atr_pct": 5.0},
                       {"symbol": "FLATA", "change_pct": 0.1, "atr_pct": 2.0}])
    monkeypatch.setattr(autopilot, "PAPER_SELECTION", "low_gap")
    assert asyncio.run(autopilot._paper_mover_symbols()) == ["FLATA"]
    monkeypatch.setattr(autopilot, "PAPER_SELECTION", "gainers")
    assert asyncio.run(autopilot._paper_mover_symbols()) == ["GAPUP", "FLATA"]   # old: any gainer, biggest first


def test_stale_board_gives_nothing(monkeypatch):
    _set(monkeypatch, [{"symbol": "FLATA", "change_pct": 0.1, "atr_pct": 2.0}])
    monkeypatch.setattr(autopilot, "_now_ist",
                        lambda: datetime(2026, 11, 26, 9, 25, tzinfo=autopilot.IST))
    assert asyncio.run(autopilot._low_gap_symbols(10)) == []
