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


def test_failed_batch_completes_the_day_instead_of_rerunning_it(monkeypatch):
    state = {"cursor": "2026-11-23", "queue": [], "queue_date": "2026-11-23",
             "symbols_remaining": ["A", "B"], "batch_idx": 2, "queue_total": 15,
             "completed_days": 0, "days_back": 0}
    async def load():
        return dict(state)
    async def save(st):
        state.clear(); state.update(st)
    async def no_start(syms, day):
        return []                                   # backend down: nothing starts
    async def bs():
        return 5
    async def not_running():
        return False
    monkeypatch.setattr(autopilot, "_backtest_allowed", lambda: True)
    monkeypatch.setattr(autopilot, "_paper_running", not_running)
    monkeypatch.setattr(autopilot, "_load_bt_state", load)
    monkeypatch.setattr(autopilot, "_save_bt_state", save)
    monkeypatch.setattr(autopilot, "_get_batch_size", bs)
    monkeypatch.setattr(autopilot, "_start_batch", no_start)
    asyncio.run(autopilot._do_backtest_step())
    assert state["symbols_remaining"] == []
    assert state["cursor"] < "2026-11-23"           # walked back, not re-run
    assert state["last_completed"] == "2026-11-23"
