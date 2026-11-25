"""Unit tests for the paper-trading supervisor guards (no network, in-memory Redis)."""
import asyncio
from datetime import datetime

import pytest

from app.services import supervisor as sv


class FakeRedis:
    def __init__(self):
        self.kv, self.h, self.sets, self.lists = {}, {}, {}, {}

    async def get(self, k): return self.kv.get(k)
    async def set(self, k, v): self.kv[k] = str(v)
    async def delete(self, k): self.kv.pop(k, None)
    async def expire(self, k, t): return True
    async def incrbyfloat(self, k, v):
        self.kv[k] = str(float(self.kv.get(k, 0)) + v)
        return float(self.kv[k])
    async def hget(self, k, f): return self.h.get(k, {}).get(f)
    async def hset(self, k, f, v): self.h.setdefault(k, {})[f] = v
    async def hdel(self, k, f): self.h.get(k, {}).pop(f, None)
    async def hexists(self, k, f): return f in self.h.get(k, {})
    async def hgetall(self, k): return dict(self.h.get(k, {}))
    async def sadd(self, k, v): self.sets.setdefault(k, set()).add(v)
    async def sismember(self, k, v): return v in self.sets.get(k, set())
    async def smembers(self, k): return set(self.sets.get(k, set()))
    async def lpush(self, k, v): self.lists.setdefault(k, []).insert(0, v)
    async def ltrim(self, k, a, b): self.lists[k] = self.lists.get(k, [])[a:b + 1]
    async def lrange(self, k, a, b): return self.lists.get(k, [])[a:b + 1]


@pytest.fixture
def r(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sv, "_redis", lambda: fake)

    async def no_ltp(symbol, max_age=20.0):
        return 0.0
    from app.utils import groww_feed
    monkeypatch.setattr(groww_feed, "get_ltp", no_ltp)

    async def no_open(exclude_id):
        return 0
    monkeypatch.setattr(sv, "_open_paper_positions", no_open)
    from app.utils import market_calendar
    monkeypatch.setattr(market_calendar, "is_trading_day", lambda d: True)
    return fake


def _now_hhmm():
    return datetime.now(sv.IST).strftime("%H:%M")


def _setup(close=100.0, high=102.0, vwap=101.0, sma5=99.0, sma20=100.0, bar_ts=None):
    now_ts = datetime.now(sv.IST).timestamp()
    candle = {"time": _now_hhmm(), "close": close, "high": close, "timestamp": now_ts}
    window = [{"time": "09:15", "close": 99.0, "high": high,
               "timestamp": now_ts - 60 if bar_ts is None else bar_ts}, candle]
    ind = {"vwap": vwap, "sma5": sma5, "sma20": sma20, "rsi": 45}
    return {"id": "s1", "symbol": "abc", "mode": "paper"}, candle, ind, window


def run(coro):
    return asyncio.run(coro)


def test_clean_entry_passes(r):
    assert run(sv.check_entry(*_setup())) is None


def test_halt_blocks(r):
    run(sv.set_halt("market looks broken"))
    assert "halted" in run(sv.check_entry(*_setup()))


def test_veto_symbol_case_insensitive(r):
    run(sv.veto_symbol("ABC", "bad scanner pick"))
    assert "vetoed" in run(sv.check_entry(*_setup()))


def test_approve_only(r):
    run(sv.update_config({"approve_only": True}))
    assert "approve-only" in run(sv.check_entry(*_setup()))
    run(sv.approve_symbol("abc", "verified"))
    assert run(sv.check_entry(*_setup())) is None


def test_daily_loss_limit(r):
    run(sv.note_exit("XYZ", -1600))
    assert "daily loss limit" in run(sv.check_entry(*_setup()))


def test_no_reentry_after_loss(r):
    run(sv.note_exit("ABC", -10))
    assert "already lost today" in run(sv.check_entry(*_setup()))


def test_stale_bar(r):
    s, candle, ind, window = _setup()
    candle["time"] = "09:15" if _now_hhmm() > "09:30" else "00:00"
    assert "stale" in run(sv.check_entry(s, candle, ind, window))


def test_near_day_high(r):
    assert "near day high" in run(sv.check_entry(*_setup(close=101.95, high=102.0)))


def test_buying_strength(r):
    assert "buying strength" in run(sv.check_entry(*_setup(close=101.5, high=105, vwap=101.0, sma5=101, sma20=100)))


def test_guard_can_be_disabled(r):
    run(sv.update_config({"guards": {"buying_strength": False}}))
    assert run(sv.check_entry(*_setup(close=101.5, high=105, vwap=101.0, sma5=101, sma20=100))) is None


def test_price_mismatch(r, monkeypatch):
    from app.utils import groww_feed

    async def ltp(symbol, max_age=20.0):
        return 110.0
    monkeypatch.setattr(groww_feed, "get_ltp", ltp)
    assert "price mismatch" in run(sv.check_entry(*_setup()))


def test_max_open(r, monkeypatch):
    async def five(exclude_id):
        return 5
    monkeypatch.setattr(sv, "_open_paper_positions", five)
    assert "max open" in run(sv.check_entry(*_setup()))


def test_fails_closed(r, monkeypatch):
    async def boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(sv, "get_config", boom)
    assert "fail closed" in run(sv.check_entry(*_setup()))


def test_force_exit_consumed_once(r):
    run(sv.request_force_exit("s1", "thesis broken"))
    assert run(sv.check_forced_exit({"id": "s1"})) == "thesis broken"
    assert run(sv.check_forced_exit({"id": "s1"})) is None


def test_market_closed_blocks(r, monkeypatch):
    from app.utils import market_calendar
    monkeypatch.setattr(market_calendar, "is_trading_day", lambda d: False)
    assert "NSE closed" in run(sv.check_entry(*_setup()))


def test_yesterdays_tape_blocks(r):
    yesterday = datetime.now(sv.IST).timestamp() - 86400
    assert "not today" in run(sv.check_entry(*_setup(bar_ts=yesterday)))


def test_holiday_calendar_knows_2026_11_24():
    from app.utils.market_calendar import is_trading_day, holiday_name
    assert not is_trading_day("2026-11-24")
    assert "Guru Nanak" in holiday_name("2026-11-24")
    assert is_trading_day("2026-11-25")


def _sim(day="2026-11-23", bar_day_ts=None, close=100.0):
    ts = bar_day_ts if bar_day_ts is not None else \
        datetime(2026, 11, 23, 10, 0, tzinfo=sv.IST).timestamp()
    return {"id": "b1", "symbol": "abc", "mode": "backtest", "date": day}, \
           {"time": "10:00", "close": close, "high": close, "timestamp": ts}


def test_sim_clean_bar_passes(r):
    s, c = _sim()
    assert run(sv.check_entry_sim(s, c, [c])) is None


def test_sim_wrong_day_tape_is_vetoed(r):
    s, c = _sim(bar_day_ts=datetime(2026, 11, 20, 10, 0, tzinfo=sv.IST).timestamp())
    assert "wrong tape" in run(sv.check_entry_sim(s, c, [c]))


def test_sim_zero_price_is_vetoed(r):
    s, c = _sim(close=0.0)
    assert "non-positive price" in run(sv.check_entry_sim(s, c, [c]))


def test_sim_ignores_strategy_guards(r, monkeypatch):
    # Training data must not be filtered by strategy opinion: a "buying strength"
    # bar near the day high on a closed-market day still passes in a backtest.
    from app.utils import market_calendar
    monkeypatch.setattr(market_calendar, "is_trading_day", lambda d: False)
    run(sv.note_exit("ABC", -5000))
    s, c = _sim()
    assert run(sv.check_entry_sim(s, c, [c])) is None


def test_unknown_config_key_rejected(r):
    with pytest.raises(ValueError):
        run(sv.update_config({"nope": 1}))


def test_sim_accepts_millisecond_timestamps(r):
    ms = datetime(2026, 11, 23, 10, 0, tzinfo=sv.IST).timestamp() * 1000
    s, c = _sim(bar_day_ts=ms)
    assert run(sv.check_entry_sim(s, c, [c])) is None


def test_live_accepts_millisecond_timestamps(r):
    now_ms = datetime.now(sv.IST).timestamp() * 1000
    s, candle, ind, window = _setup(bar_ts=now_ms - 60_000)
    assert run(sv.check_entry(s, candle, ind, window)) is None
