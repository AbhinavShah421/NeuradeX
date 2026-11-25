import asyncio

from app import autopilot


def test_backtest_uses_the_days_opening_movers(monkeypatch):
    async def movers(day, n=autopilot.BT_MOVERS_N):
        assert day == "2026-11-23"
        return ["SHALPAINTS", "ASHOKA", "HITECH", "RATNAVEER"]
    monkeypatch.setattr(autopilot, "BT_SELECTION", "open_movers")
    monkeypatch.setattr(autopilot, "_open_mover_symbols", movers)
    syms, sel = asyncio.run(autopilot._backtest_symbols("2026-11-23"))
    assert sel == "open_movers"
    assert syms == ["SHALPAINTS", "ASHOKA", "HITECH", "RATNAVEER"]   # gap order kept


def test_thin_or_missing_board_falls_back_to_fixed_universe(monkeypatch):
    async def movers(day, n=autopilot.BT_MOVERS_N):
        return ["ONLYONE"]
    async def ranked(pool, day):
        return sorted(pool)
    monkeypatch.setattr(autopilot, "BT_SELECTION", "open_movers")
    monkeypatch.setattr(autopilot, "_open_mover_symbols", movers)
    monkeypatch.setattr(autopilot, "_rank_by_historical_sentiment", ranked)
    syms, sel = asyncio.run(autopilot._backtest_symbols("2026-11-23"))
    assert sel == "fixed_universe" and set(syms) == set(autopilot._BT_UNIVERSE)


def test_fixed_selection_ignores_the_board(monkeypatch):
    async def movers(day, n=autopilot.BT_MOVERS_N):
        raise AssertionError("board must not be fetched in fixed mode")
    async def ranked(pool, day):
        return pool
    monkeypatch.setattr(autopilot, "BT_SELECTION", "fixed")
    monkeypatch.setattr(autopilot, "_open_mover_symbols", movers)
    monkeypatch.setattr(autopilot, "_rank_by_historical_sentiment", ranked)
    syms, sel = asyncio.run(autopilot._backtest_symbols("2026-11-23"))
    assert sel == "fixed_universe"


def test_reset_cursor_drops_the_old_days_pending_symbols(monkeypatch):
    saved = {}
    async def load():
        return {"cursor": "2026-09-08", "queue": [], "symbols_remaining": ["SBIN", "TCS"], "batch_idx": 2}
    async def save(st):
        saved.update(st)
    monkeypatch.setattr(autopilot, "_load_bt_state", load)
    monkeypatch.setattr(autopilot, "_save_bt_state", save)
    asyncio.run(autopilot.reset_cursor())
    assert saved["symbols_remaining"] == [] and saved["batch_idx"] == 0
    assert saved["cursor"] != "2026-09-08"
