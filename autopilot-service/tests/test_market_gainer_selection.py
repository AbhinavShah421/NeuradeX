import asyncio
import json
from datetime import datetime

from app import autopilot


class FakeRedis:
    def __init__(self, value):
        self.value = value

    async def get(self, key):
        assert key == autopilot.MOVERS_KEY
        return self.value


def _set_movers(monkeypatch, payload):
    async def get_redis():
        return FakeRedis(json.dumps(payload))

    monkeypatch.setattr(autopilot, "_get_redis", get_redis)
    monkeypatch.setattr(
        autopilot,
        "_now_ist",
        lambda: datetime(2026, 10, 28, 10, 46, tzinfo=autopilot.IST),
    )


def test_top_gainers_are_fresh_positive_movers_in_change_order(monkeypatch):
    _set_movers(monkeypatch, {
        "updated_at": "2026-10-28T10:40:00+05:30",
        "items": [
            {"symbol": "B", "change_pct": 5.0},
            {"symbol": "A", "change_pct": 5.0},
            {"symbol": "C", "change_pct": 0.0},
            {"symbol": "D", "change_pct": -1.0},
            {"symbol": "A", "change_pct": 4.0},
            {"symbol": "BAD/SYM", "change_pct": 10.0},
            {"symbol": "E", "change_pct": "9.0"},
            {"symbol": "F", "change_pct": float("inf")},
        ],
    })

    assert asyncio.run(autopilot._top_gainer_symbols(10)) == ["A", "B"]


def test_stale_or_untimestamped_gainers_are_not_selected(monkeypatch):
    _set_movers(monkeypatch, {
        "updated_at": "2026-10-28T06:00:00+05:30",
        "items": [{"symbol": "A", "change_pct": 5.0}],
    })
    assert asyncio.run(autopilot._top_gainer_symbols()) == []

    _set_movers(monkeypatch, {
        "items": [{"symbol": "A", "change_pct": 5.0}],
    })
    assert asyncio.run(autopilot._top_gainer_symbols()) == []


def test_paper_candidates_prioritize_gainers_and_dedupe():
    assert autopilot._paper_candidate_symbols(
        ["gainer1", "shared"], ["committed1", "shared"], ["promoted1", "committed1"]
    ) == ["GAINER1", "SHARED", "COMMITTED1", "PROMOTED1"]
