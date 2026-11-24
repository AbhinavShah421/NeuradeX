import asyncio
import json

import pytest
from fastapi import HTTPException

from app.services import ai_engine_service
from app.utils import redis_client


class FakeRedis:
    def __init__(self, value):
        self.value = value

    async def get(self, key):
        assert key == "ai_engine:movers"
        return self.value


def test_market_gainers_returns_positive_movers_and_scan_metadata(monkeypatch):
    payload = {
        "updated_at": "2026-10-28T10:14:56+05:30",
        "scanned": 2367,
        "universe": 2367,
        "items": [
            {"symbol": "AAA", "change_pct": 12.5},
            {"symbol": "BBB", "change_pct": 0},
            {"symbol": "CCC", "change_pct": -1},
            {"symbol": "DDD", "change_pct": "2"},
            None,
        ],
    }
    monkeypatch.setattr(redis_client, "get_redis", lambda: FakeRedis(json.dumps(payload)))

    result = asyncio.run(ai_engine_service.get_market_gainers(limit=1))

    assert result["data"]["updated_at"] == payload["updated_at"]
    assert result["data"]["scanned"] == 2367
    assert result["data"]["universe"] == 2367
    assert result["data"]["items"] == [{"symbol": "AAA", "change_pct": 12.5}]


def test_market_gainers_surfaces_redis_failures(monkeypatch):
    class BrokenRedis:
        async def get(self, _key):
            raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(redis_client, "get_redis", lambda: BrokenRedis())

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_engine_service.get_market_gainers())

    assert error.value.status_code == 503
