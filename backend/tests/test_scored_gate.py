"""Behavioural tests for the scored entry gate (2026-07-14).

The gate used to be a hard AND of ~13 conditions whose joint pass-probability
collapsed to ~zero (two full 0-trade days on 2026-07-13/14). These tests lock
the new contract:

  • a textbook setup enters,
  • ONE marginal shortfall (RSI 56, or thin net consensus) still enters,
  • TWO shortfalls together do not,
  • the risk stops (trusted dissent, ensemble veto, warm-up) stay HARD blocks.

Direction was INVERTED on 2026-08-10 against 256,805 CF-labelled decisions:
the gate now blocks chasing strength (price above VWAP with SMA5 > SMA20) and
admits the setup it used to call a falling knife. `WEAK_IND` is therefore the
clean-tape fixture and `STRENGTH_IND` the blocked one.

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


# The favoured tape after the 2026-08-10 direction inversion: price BELOW VWAP,
# SMA5 < SMA20, RSI < 45. Scores the same 25 trend + 25 RSI points that
# STRENGTH_IND used to score under the old rules, so every test that just wants
# "a clean tape" keeps its original arithmetic.
WEAK_IND = {"rsi": 40.0, "vwap": 101.5, "sma5": 99.5, "sma20": 100.5,
            "mom5": -0.1, "atr": 0.2}

# What the gate used to require and now hard-blocks: price above VWAP with
# SMA5 > SMA20. Measured worst cell (22.6% win vs 28.6% for everything else)
# across 256,805 CF-labelled decisions, day-level paired t = +3.49.
STRENGTH_IND = {"rsi": 63.0, "vwap": 100.0, "sma5": 100.4, "sma20": 100.1,
                "mom5": 0.1, "atr": 0.2}

BUY3 = [  # 3 BUY incl. a reliable voter (gbm), no SELL → consensus 30 + co-sign 10
    {"agent_name": "gbm", "action": "BUY", "confidence": 0.64},
    {"agent_name": "pattern", "action": "BUY", "confidence": 0.60},
    {"agent_name": "momentum", "action": "BUY", "confidence": 0.55},
    {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
]

THIN_NET = [  # 2 BUY - 1 non-structural SELL = net 1 → consensus 15
    {"agent_name": "gbm", "action": "BUY", "confidence": 0.64},
    {"agent_name": "momentum", "action": "BUY", "confidence": 0.55},
    {"agent_name": "technical", "action": "SELL", "confidence": 0.60},
]


def _run(monkeypatch, agents, ind, ens_action="HOLD", conf=0.60, veto="", bars=30,
         legacy_filter=False):
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
    # Pin the direction-gate mode explicitly. _TREND_FILTER_LEGACY is read
    # from the environment at import, so without this the suite's result
    # depends on how the host happens to be deployed: it went red on
    # 2026-08-20 when the live filter was rolled back to legacy, which was a
    # test-isolation bug rather than a behaviour regression. Most scenarios
    # here exercise the inverted branch's scoring, hence the default.
    monkeypatch.setattr(svc, "_TREND_FILTER_LEGACY", legacy_filter)
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
    s = _run(monkeypatch, BUY3, WEAK_IND)
    assert s["position"]["status"] == "LONG"
    assert s["last_decision"]["executed"] is True
    assert "Entry [score" in s["last_decision"]["reason"]


def test_rsi_is_never_a_block(monkeypatch):
    # RSI was neutralised 2026-07-28, then partly restored 2026-08-10 as a
    # 5-point tilt: <45 while below VWAP is worth +5 (measured +0.0394, t=2.22
    # inside that stratum only). It is a tilt, never a veto — 95 with the tilt,
    # 90 without, and both clear gentle's 78.
    for rsi in (40.0, 56.0, 63.0, 78.0):
        s = _run(monkeypatch, BUY3, dict(WEAK_IND, rsi=rsi))
        assert s["position"]["status"] == "LONG", f"RSI {rsi} should not block"


def test_rsi_tilt_only_applies_below_vwap(monkeypatch):
    # Above VWAP the same RSI carries no measured edge (t = -0.33), so it must
    # not be paid for there. Above-VWAP-only is a single shortfall: it keeps
    # the 12 SMA points but loses both the 13 VWAP points and the 5 RSI tilt.
    ind = dict(WEAK_IND, rsi=40.0, vwap=100.0)   # price above VWAP, SMA5<SMA20
    s = _run(monkeypatch, BUY3, ind)
    assert "no oversold-below-VWAP edge" in s["last_decision"]["reason"]


def test_one_shortfall_thin_net_still_enters(monkeypatch):
    # Net consensus 1 costs 15 (30→15, repriced 2026-07-17 — CF labels put
    # net=1 in a 25%-win class): 15+10+25+25+5 = 80 ≥ 78. Alone on a perfect
    # tape it still enters; the old gate's "panel dissent" rule blocked it
    # outright.
    s = _run(monkeypatch, THIN_NET, WEAK_IND)
    assert s["position"]["status"] == "LONG"


def test_two_shortfalls_block(monkeypatch):
    # Quality debt still compounds. Thin net (15) AND price above VWAP, which
    # now costs the 13 trend points and the 5 RSI tilt with it:
    # 15 + 10 + 12(sma leg only) + 20 + 5 = 62 < 78.
    ind = dict(WEAK_IND, vwap=100.0)   # price above VWAP, SMA5 still < SMA20
    s = _run(monkeypatch, THIN_NET, ind)
    assert s["position"]["status"] == "NONE"
    assert "entry score 62 < 78" in s["last_decision"]["reason"]


# ── Direction filter: inverted 2026-08-10 ────────────────────────────────────

def test_chasing_strength_is_hard_block(monkeypatch):
    # Price above VWAP with SMA5 > SMA20 — what the gate used to REQUIRE — is
    # the measured worst cell (22.6% win vs 28.6% for the rest, paired
    # t = +3.49 over 23 days). It now blocks no matter how strong the panel is.
    s = _run(monkeypatch, BUY3, STRENGTH_IND)
    assert s["position"]["status"] == "NONE"
    assert "chasing strength" in s["last_decision"]["reason"]


def test_falling_knife_now_enters(monkeypatch):
    # The exact setup the old gate hard-blocked as a "falling knife" is the one
    # that measured best (+0.1168 pts, t = +3.00, positive on 18/23 days).
    s = _run(monkeypatch, BUY3, WEAK_IND)
    assert s["position"]["status"] == "LONG"
    assert "falling knife" not in s["last_decision"]["reason"]


def test_one_trend_leg_each_way_still_enters(monkeypatch):
    # Only one leg pointing the favoured way is a single shortfall, not a block:
    # below VWAP but SMA5 > SMA20 → 30 + 10 + 13 + 25 + 5 = 83 ≥ 78.
    ind = dict(WEAK_IND, sma5=100.5, sma20=99.5)
    s = _run(monkeypatch, BUY3, ind)
    assert s["position"]["status"] == "LONG"


def test_legacy_trend_filter_restores_old_behaviour(monkeypatch):
    # One env var must put the pre-inversion gate back, because this changes
    # live entry direction and has to be reversible in a single step.
    s = _run(monkeypatch, BUY3, STRENGTH_IND, legacy_filter=True)
    assert s["position"]["status"] == "LONG", "legacy mode should buy strength again"
    s2 = _run(monkeypatch, BUY3, WEAK_IND, legacy_filter=True)
    assert s2["position"]["status"] == "NONE"
    assert "falling knife" in s2["last_decision"]["reason"]


def test_rsi_below_45_no_longer_blocks(monkeypatch):
    # Was a hard veto ("failing bounce", justified on 8 CF samples). At n=11,177
    # entry-eligible decisions the <45 band hit 32.1% — the second-best band of
    # five — so vetoing it was discarding an average-or-better population.
    ind = dict(WEAK_IND, rsi=40.0)
    s = _run(monkeypatch, BUY3, ind)
    assert s["position"]["status"] == "LONG"
    assert "failing bounce" not in s["last_decision"]["reason"]


def test_trusted_dissent_is_hard_block(monkeypatch):
    # A non-structural reliable agent SELLing at ≥0.75 blocks even a 95-scorer.
    agents = BUY3 + [{"agent_name": "rl", "action": "SELL", "confidence": 0.80}]
    s = _run(monkeypatch, agents, WEAK_IND)
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
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "LONG"


def test_confident_day_structure_sell_still_vetoes(monkeypatch):
    # The structural exemption is only about the dissent COUNT — day_structure's
    # own dedicated veto (SELL ≥ 0.62: top of range, poor R/R) must still block.
    agents = BUY3 + [{"agent_name": "day_structure", "action": "SELL",
                      "confidence": 0.88, "indicators": {}}]
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "NONE"
    assert "day-structure veto" in s["last_decision"]["reason"]


def test_ensemble_veto_is_hard_block(monkeypatch):
    s = _run(monkeypatch, BUY3, WEAK_IND, veto="anomaly veto: test")
    assert s["position"]["status"] == "NONE"
    assert "ensemble veto honored" in s["last_decision"]["reason"]


def test_warmup_blocks_early_entries(monkeypatch):
    # Below _WARMUP_BARS the indicator helpers return neutral placeholders
    # (RSI exactly 50.0) — a textbook-looking setup on 15 bars must NOT enter
    # (2026-07-15: two first-15-min entries fired on default indicators).
    s = _run(monkeypatch, BUY3, WEAK_IND, bars=15)
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
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "NONE"
    assert "memory BUY not counted" in s["last_decision"]["reason"]


def test_day_structure_buy_not_counted_as_consensus(monkeypatch):
    # day_structure's BUY is a range-position statement, not directional
    # conviction — sentiment + day_structure alone is a 1-voter consensus
    # (2026-07-16 BANKINDIA, -0.25%: entered on exactly this pair with rl
    # voting SELL).
    agents = [
        {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
        {"agent_name": "day_structure", "action": "BUY", "confidence": 0.70,
         "indicators": {}},
        {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
    ]
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "NONE"
    assert "insufficient BUY consensus" in s["last_decision"]["reason"]


def test_memory_vote_with_real_precedent_counts(monkeypatch):
    # The same consensus with memory recalling plenty of cases is legitimate.
    agents = [
        {"agent_name": "sentiment", "action": "BUY", "confidence": 0.64},
        {"agent_name": "memory", "action": "BUY", "confidence": 0.79,
         "indicators": {"n_BUY": 12, "wr_BUY": 0.62}},
        {"agent_name": "technical", "action": "HOLD", "confidence": 0.50},
    ]
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "LONG"


# ── Anti-predictive SELL voters don't cancel BUY votes ───────────────────────
# 2026-07-31 HEXT: 197 bars blocked on "panel dissent: 2 BUY - 2 SELL = 0"
# through a move that rose in 86.6% of 60-minute windows. The consensus count
# treated every SELL as equal evidence, so votes from agents that are
# ANTI-predictive when bearish cancelled votes from agents that are not.

def test_unreliable_sellers_excluded_from_consensus():
    from app.services.sessions_service import _DISCOUNTED_SELLERS
    for agent in ("momentum", "pattern", "regime", "volatility"):
        assert agent in _DISCOUNTED_SELLERS, agent


def test_sellers_that_flip_sign_keep_their_vote():
    # gbm (+0.015 -> -0.083) and technical (+0.015 -> -0.015) flip across the
    # day-split, so there is no stable basis to discount them. Conservative
    # default: leave the gate as it is.
    from app.services.sessions_service import _DISCOUNTED_SELLERS
    assert "gbm" not in _DISCOUNTED_SELLERS
    assert "technical" not in _DISCOUNTED_SELLERS


def test_genuinely_bearish_seller_still_counts():
    # rl is negative in BOTH halves (-0.007 -> -0.031): a SELL worth counting.
    from app.services.sessions_service import _DISCOUNTED_SELLERS
    assert "rl" not in _DISCOUNTED_SELLERS


def test_structural_sellers_still_exempt():
    from app.services.sessions_service import _STRUCTURAL_SELLERS, _DISCOUNTED_SELLERS
    assert _STRUCTURAL_SELLERS <= _DISCOUNTED_SELLERS
    assert {"day_structure", "meanrev"} == set(_STRUCTURAL_SELLERS)


def test_anti_predictive_sells_no_longer_block_entry(monkeypatch):
    # 3 BUY vs 2 SELL, both SELLs from anti-predictive agents. Previously
    # net = 3-2 = 1 ("thin net", -15); now they don't count, so net = 3.
    agents = BUY3 + [
        {"agent_name": "regime", "action": "SELL", "confidence": 0.60},
        {"agent_name": "momentum", "action": "SELL", "confidence": 0.60},
    ]
    s = _run(monkeypatch, agents, WEAK_IND)
    assert s["position"]["status"] == "LONG"
    assert "thin net consensus" not in s["last_decision"]["reason"]
