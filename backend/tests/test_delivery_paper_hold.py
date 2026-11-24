"""Delivery paper: hold the graded horizon, never act on an unfetched price, benchmark vs Nifty."""
import asyncio

from app.api import delivery_paper as dp

CFG = dict(dp.DEFAULT_CONFIG)


def _pos(**kw):
    p = {"symbol": "ABC", "entry_date": "2026-11-02", "entry_price": 100.0, "qty": 10,
         "target": 112.0, "stop": 94.0, "target_pct": 12.0, "stop_pct": 6.0, "current": 100.0}
    p.update(kw)
    return p


def test_downgrade_waits_for_min_hold():
    exit_now, why = dp.DeliveryAgent.decide_exit(_pos(), 101.0, 1, CFG, False, None)
    assert not exit_now and "graded horizon" in why


def test_downgrade_exits_after_min_hold():
    exit_now, why = dp.DeliveryAgent.decide_exit(_pos(), 101.0, 5, CFG, False, None)
    assert exit_now and "downgrade" in why


def test_stop_fires_inside_min_hold():
    exit_now, why = dp.DeliveryAgent.decide_exit(_pos(), 93.0, 1, CFG, True, "A")
    assert exit_now and "Stop" in why


def _patch(monkeypatch, pfs, prices, nifty=20000.0, picks=()):
    saved = {}

    async def load(): return pfs
    async def save(x): saved["pfs"] = x
    async def get_picks(): return list(picks)
    async def get_prices(syms): return dict(prices)
    async def get_nifty(): return nifty
    async def rset(k, v, ttl=0): return None
    async def rec(*a): return None
    async def levels(sym, price, cfg):
        return {"target": price * 1.12, "stop": price * 0.94, "target_pct": 12.0,
                "stop_pct": 6.0, "src": "config", "forecast": None}
    for name, fn in (("_load_pfs", load), ("_save_pfs", save), ("_delivery_picks", get_picks),
                     ("_prices", get_prices), ("_nifty", get_nifty), ("_rset", rset),
                     ("_record_delivery_outcome", rec), ("_forecast_levels", levels)):
        monkeypatch.setattr(dp, name, fn)
    return saved


def _pf(positions):
    return {"p1": {"id": "p1", "name": "t", "capital": 1000.0, "cash": 0.0, "config": {},
                   "positions": positions, "closed": [], "value": 1000.0, "managed": True}}


def test_missing_quote_defers_exit(monkeypatch):
    pfs = _pf([_pos(entry_date="2026-10-01")])        # well past every hold limit
    _patch(monkeypatch, pfs, prices={})
    asyncio.run(dp.tick("test"))
    assert len(pfs["p1"]["positions"]) == 1 and not pfs["p1"]["closed"]
    assert "no live quote" in pfs["p1"]["positions"][0]["status_reason"]


def test_exit_records_excess_over_nifty(monkeypatch):
    pfs = _pf([_pos(entry_date="2026-10-01", nifty_entry=20000.0)])
    _patch(monkeypatch, pfs, prices={"ABC": 103.0}, nifty=20200.0)
    asyncio.run(dp.tick("test"))
    c = pfs["p1"]["closed"][0]
    assert c["pnl_pct"] == 3.0 and c["nifty_pct"] == 1.0 and c["excess_pct"] == 2.0


def test_no_entry_without_fetched_price(monkeypatch):
    pfs = _pf([])
    pfs["p1"]["cash"] = 10000.0
    _patch(monkeypatch, pfs, prices={}, picks=[{"symbol": "XYZ", "price": 50.0, "grade": "A"}])
    asyncio.run(dp.tick("test"))
    assert pfs["p1"]["positions"] == []
