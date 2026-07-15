"""Behavioural tests for the scored entry gate (2026-07-14).

The gate used to be a hard AND of ~13 conditions whose joint pass-probability
collapsed to ~zero (two full 0-trade days on 2026-07-13/14). These tests lock
the new contract:

  • a textbook setup enters,
  • ONE marginal shortfall (RSI 56, or thin net consensus) still enters,
  • TWO shortfalls together do not,
  • the risk stops (falling knife, RSI<45, trusted dissent) stay HARD blocks.

Everything external (ensemble, indicators, timing signal, throttle, gate mode)
is monkeypatched so _step runs as a pure scenario machine.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.services.sessions_service as svc


# ── Scenario harness ──────────────────────────────────────────────────────────

def _window(n: int = 30) -> list[dict]:
    bars = []
    px = 100.0
    for i in range(n):
        o = px
        px = round(px + 0.02, 4)
        bars.append({"time": f"10:{i:02d}", "timestamp": 1784000000 + i * 60,
                     "open": o, "high": px, "low": o, "close": px, "volume": 1000})
    return bars


def _session() -> dict:
    return {
        "id": "test-gate", "symbol": "TEST", "mode": "replay", "date": "2026-07-14",
        "capital": 50000, "cash": 50000.0, "trades": [], "decision_log": [],
        "metrics": {}, "max_hold_minutes": 0,
        "position": {"status": "NONE", "entry_price": 0.0, "quantity": 0,
                     "entry_time": None, "current_pnl": 0.0},
    }


UPTREND_IND = {"rsi": 63.0, "vwap": 100.0, "sma5": 100.4, "sma20": 100.1,
               "mom5": 0.1, "atr": 0.2}

BUY3 = [  # 3 BUY incl. a reliable voter, no SELL → consensus 30 + co-sign 10
    {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
    {"agent_name": "pattern", "action": "BUY", "confidence": 0.60},
    {"agent_name": "momentum", "action": "BUY", "confidence": 0.55},
    {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
]

THIN_NET = [  # 2 BUY - 1 non-structural SELL = net 1 → consensus 22
    {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
    {"agent_name": "momentum", "action": "BUY", "confidence": 0.55},
    {"agent_name": "technical", "action": "SELL", "confidence": 0.60},
]


def _run(monkeypatch, agents, ind, ens_action="HOLD", conf=0.60, veto="", bars=30):
    decision = SimpleNamespace(action=ens_action, confidence=conf, reasoning="test",
                               veto=veto, vote_mode="directional",
                               prediction_id="pid-test")

    async def fake_gate():
        return "gentle"

    async def fake_ensemble(*a, **k):
        return decision, agents

    async def fake_throttle(symbol):
        return None

    monkeypatch.setattr(svc, "get_trade_gate", fake_gate)
    monkeypatch.setattr(svc, "_ensemble_decision", fake_ensemble)
    monkeypatch.setattr(svc, "_symbol_throttle_reason", fake_throttle)
    monkeypatch.setattr(svc, "_intraday_indicators", lambda w, i: dict(ind))
    monkeypatch.setattr(svc, "_tech_signal", lambda *a, **k: 0)
    # Pattern-quality gate: raise inside its try → skipped (not under test here).
    import app.agents
    monkeypatch.setattr(app.agents, "get_pattern_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("no pattern engine")))

    s = _session()
    asyncio.run(svc._step(s, _window(bars), force_close=False))
    return s


# ── The relaxations: marginal shortfalls score, they don't zero ──────────────

def test_textbook_setup_enters(monkeypatch):
    # 30 + 10 + 25 + 25 + 5 = 95 ≥ 78
    s = _run(monkeypatch, BUY3, UPTREND_IND)
    assert s["position"]["status"] == "LONG"
    assert s["last_decision"]["executed"] is True
    assert "Entry [score" in s["last_decision"]["reason"]


def test_one_shortfall_rsi56_still_enters(monkeypatch):
    # RSI 56 costs 11 (25→14): 30+10+25+14+5 = 84 ≥ 78. The old AND-gate
    # blocked this outright ("insufficient strength").
    ind = dict(UPTREND_IND, rsi=56.0)
    s = _run(monkeypatch, BUY3, ind)
    assert s["position"]["status"] == "LONG"


def test_one_shortfall_thin_net_still_enters(monkeypatch):
    # Net consensus 1 costs 8 (30→22): 22+10+25+25+5 = 87 ≥ 78. The old gate's
    # "panel dissent" rule blocked this outright.
    s = _run(monkeypatch, THIN_NET, UPTREND_IND)
    assert s["position"]["status"] == "LONG"


def test_two_shortfalls_block(monkeypatch):
    # Thin net AND weak RSI: 22+10+25+14+5 = 76 < 78 — quality debt compounds.
    ind = dict(UPTREND_IND, rsi=56.0)
    s = _run(monkeypatch, THIN_NET, ind)
    assert s["position"]["status"] == "NONE"
    assert "entry score 76 < 78" in s["last_decision"]["reason"]


# ── The risk stops stay hard ─────────────────────────────────────────────────

def test_falling_knife_is_hard_block(monkeypatch):
    # Both trend legs down blocks no matter how strong the panel is.
    ind = dict(UPTREND_IND, vwap=101.5, sma5=99.5, sma20=100.5)
    s = _run(monkeypatch, BUY3, ind)
    assert s["position"]["status"] == "NONE"
    assert "falling knife" in s["last_decision"]["reason"]


def test_rsi_below_45_is_hard_block(monkeypatch):
    ind = dict(UPTREND_IND, rsi=40.0)
    s = _run(monkeypatch, BUY3, ind)
    assert s["position"]["status"] == "NONE"
    assert "failing bounce" in s["last_decision"]["reason"]


def test_trusted_dissent_is_hard_block(monkeypatch):
    # A non-structural reliable agent SELLing at ≥0.75 blocks even a 95-scorer.
    agents = BUY3 + [{"agent_name": "rl", "action": "SELL", "confidence": 0.80}]
    s = _run(monkeypatch, agents, UPTREND_IND)
    assert s["position"]["status"] == "NONE"
    assert "trusted-expert dissent" in s["last_decision"]["reason"]


def test_structural_sellers_dont_count_as_dissent(monkeypatch):
    # meanrev SELLs by construction against exactly the strength we buy (fade
    # the rip) — it must count neither toward net consensus nor as trusted
    # dissent (it IS in _RELIABLE_BUY_AGENTS), even at 0.92. A sub-veto
    # day_structure SELL (<0.62 keeps its dedicated veto quiet) is likewise a
    # position-in-range statement, not dissent. 3 real BUYs still clear.
    agents = BUY3 + [
        {"agent_name": "day_structure", "action": "SELL", "confidence": 0.55,
         "indicators": {}},
        {"agent_name": "meanrev", "action": "SELL", "confidence": 0.92},
    ]
    s = _run(monkeypatch, agents, UPTREND_IND)
    assert s["position"]["status"] == "LONG"


def test_confident_day_structure_sell_still_vetoes(monkeypatch):
    # The structural exemption is only about the dissent COUNT — day_structure's
    # own dedicated veto (SELL ≥ 0.62: top of range, poor R/R) must still block.
    agents = BUY3 + [{"agent_name": "day_structure", "action": "SELL",
                      "confidence": 0.88, "indicators": {}}]
    s = _run(monkeypatch, agents, UPTREND_IND)
    assert s["position"]["status"] == "NONE"
    assert "day-structure veto" in s["last_decision"]["reason"]


def test_ensemble_veto_is_hard_block(monkeypatch):
    s = _run(monkeypatch, BUY3, UPTREND_IND, veto="anomaly veto: test")
    assert s["position"]["status"] == "NONE"
    assert "ensemble veto honored" in s["last_decision"]["reason"]


def test_warmup_blocks_early_entries(monkeypatch):
    # Below _WARMUP_BARS the indicator helpers return neutral placeholders
    # (RSI exactly 50.0) — a textbook-looking setup on 15 bars must NOT enter
    # (2026-07-15: two first-15-min entries fired on default indicators).
    s = _run(monkeypatch, BUY3, UPTREND_IND, bars=15)
    assert s["position"]["status"] == "NONE"
    assert "indicator warm-up" in s["last_decision"]["reason"]


def test_memory_cold_start_vote_not_counted(monkeypatch):
    # sentiment + a memory BUY backed by a single similar case is not a real
    # 2-voter consensus (2026-07-15 PURVA, -0.95%).
    agents = [
        {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
        {"agent_name": "memory", "action": "BUY", "confidence": 0.79,
         "indicators": {"n_BUY": 1}},
        {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
    ]
    s = _run(monkeypatch, agents, UPTREND_IND)
    assert s["position"]["status"] == "NONE"
    assert "memory BUY not counted" in s["last_decision"]["reason"]


def test_memory_vote_with_real_precedent_counts(monkeypatch):
    # The same consensus with memory recalling plenty of cases is legitimate.
    agents = [
        {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
        {"agent_name": "memory", "action": "BUY", "confidence": 0.79,
         "indicators": {"n_BUY": 12, "wr_BUY": 0.62}},
        {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
    ]
    s = _run(monkeypatch, agents, UPTREND_IND)
    assert s["position"]["status"] == "LONG"
