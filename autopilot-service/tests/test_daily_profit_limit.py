import asyncio
from datetime import datetime

from app import autopilot


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.writes = {}

    async def get(self, key):
        return self.value if key == autopilot.PAPER_PROFIT_HALT_KEY else None

    async def set(self, key, value, **kwargs):
        self.writes[key] = (value, kwargs)


def test_today_paper_profit_includes_running_sessions_and_excludes_other_days():
    sessions = [
        {"mode": "paper", "created_at": "2026-11-03T10:00:00+05:30", "status": "running", "pnl": 1200},
        {"mode": "paper", "created_at": "2026-11-03T10:01:00+05:30", "status": "done", "pnl": 900},
        {"mode": "paper", "created_at": "2026-11-02T10:00:00+05:30", "pnl": 5000},
        {"mode": "backtest", "created_at": "2026-11-03T10:00:00+05:30", "pnl": 5000},
    ]

    assert autopilot._today_paper_profit(sessions, "2026-11-03") == 2100


def test_daily_profit_over_limit_sets_day_halt(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(autopilot, "_market_open", lambda: True)
    monkeypatch.setattr(
        autopilot, "_now_ist",
        lambda: datetime(2026, 11, 3, 10, 0, tzinfo=autopilot.IST),
    )

    async def get_redis():
        return fake_redis

    async def daily_profit():
        return autopilot._DAILY_PROFIT_LIMIT + 0.01

    monkeypatch.setattr(autopilot, "_get_redis", get_redis)
    monkeypatch.setattr(autopilot, "_daily_paper_profit", daily_profit)

    asyncio.run(autopilot._do_paper_tick())

    assert fake_redis.writes[autopilot.PAPER_PROFIT_HALT_KEY] == (
        "2026-11-03", {"ex": 86400 * 2}
    )


def test_profit_at_limit_does_not_halt(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(autopilot, "_market_open", lambda: True)
    monkeypatch.setattr(
        autopilot, "_now_ist",
        lambda: datetime(2026, 11, 3, 10, 0, tzinfo=autopilot.IST),
    )

    async def get_redis():
        return fake_redis

    async def daily_profit():
        return autopilot._DAILY_PROFIT_LIMIT

    async def daily_pnl_pct():
        return 0.0

    async def no_symbols(*args, **kwargs):
        return []

    monkeypatch.setattr(autopilot, "_get_redis", get_redis)
    monkeypatch.setattr(autopilot, "_daily_paper_profit", daily_profit)
    monkeypatch.setattr(autopilot, "_daily_pnl_pct", daily_pnl_pct)
    monkeypatch.setattr(autopilot, "_top_gainer_symbols", no_symbols)
    monkeypatch.setattr(autopilot, "_committed_symbols", no_symbols)
    monkeypatch.setattr(autopilot, "_live_promoted_symbols", no_symbols)
    monkeypatch.setattr(autopilot, "_top_conviction_symbols", no_symbols)

    asyncio.run(autopilot._do_paper_tick())

    assert autopilot.PAPER_PROFIT_HALT_KEY not in fake_redis.writes
