"""The pre-execution validator must block, and must never approve.

Two properties matter more than any individual rule:

  1. It can only ever turn a BUY into a HOLD. Nothing in it can create a trade.
  2. A check that cannot run blocks, rather than being treated as passed. It is
     the last thing between a decision and real money; an unusable validator has
     to fail closed.

The specific rules are pinned against failures that actually reached production
here — a zero entry price, and a zero-volume in-progress candle.
"""
import pytest

from app.agents.validator import CHECKS, Verdict, build_context, validate_entry

GATE = {"label": "Gentle", "score_min": 78, "min_conf": 0.50, "max_conf": 0.68,
        "min_buy": 2, "require_buy": False}


def ctx(**over):
    """A decision that passes everything, so each test changes exactly one thing."""
    base = build_context(
        symbol="RELIANCE",
        candle={"time": "10:15", "close": 1204.5, "volume": 48210},
        gate=GATE, score=86.0, confidence=0.61, buy_votes=3,
        position_status="FLAT", mode="paper",
        session={"market_open": True},
    )
    base.update(over)
    return base


def test_a_clean_entry_passes():
    v = validate_entry(ctx())
    assert v.ok, v.reasons
    assert v.checked == len(CHECKS)


# ── Price: the 90-husk failure ──────────────────────────────────────────────

@pytest.mark.parametrize("price", [0, 0.0, -5.0])
def test_a_trade_cannot_be_priced_at_or_below_zero(price):
    """Ninety records were stored with entry_price 0 because a field name did
    not match across a service boundary, and nothing errored."""
    v = validate_entry(ctx(price=price))
    assert not v.ok
    assert any("zero" in r or "priced" in r for r in v.reasons)


def test_a_nan_price_is_blocked():
    """NaN is the value that would slip furthest: it compares false against
    every threshold, so it passes a `< limit` gate silently."""
    v = validate_entry(ctx(price=float("nan")))
    assert not v.ok
    assert any("finite" in r for r in v.reasons)


def test_a_missing_price_is_blocked():
    assert not validate_entry(ctx(price=None)).ok


# ── Bar completeness: the forming-candle failure ────────────────────────────

def test_a_zero_volume_bar_is_not_tradable():
    """A zero-volume in-progress candle made the anomaly agent veto half of all
    decisions for two days. The same bad input must be recognised here."""
    v = validate_entry(ctx(candle={"time": "10:15", "close": 1204.5, "volume": 0}))
    assert not v.ok
    assert any("zero volume" in r for r in v.reasons)


def test_absent_volume_is_not_treated_as_zero():
    """A feed that does not report volume is different from a bar that did not
    trade. Blocking on the former would stop every entry on that feed."""
    assert validate_entry(ctx(candle={"time": "10:15", "close": 1204.5})).ok


# ── Gate self-consistency ──────────────────────────────────────────────────

def test_a_score_under_the_gate_is_blocked():
    """Catches an edited veto chain: `enter` is a long conjunction, and if a
    future change drops a term the score can fall under while enter stays true."""
    v = validate_entry(ctx(score=61.0))
    assert not v.ok
    assert any("below the Gentle gate" in r for r in v.reasons)


def test_confidence_outside_the_gate_band_is_blocked():
    assert not validate_entry(ctx(confidence=0.42)).ok      # under the floor
    assert not validate_entry(ctx(confidence=0.80)).ok      # over the ceiling


def test_require_buy_gate_needs_the_votes():
    strict = {**GATE, "label": "Strict", "require_buy": True, "min_buy": 3}
    assert not validate_entry(ctx(gate=strict, buy_votes=1)).ok
    assert validate_entry(ctx(gate=strict, buy_votes=3)).ok


# ── The measured anti-predictive band ──────────────────────────────────────

def test_the_anti_predictive_band_is_blocked_even_if_the_gate_allows_it():
    """Trading Controls can widen max_conf to 1.05. Entering above 0.90 — where
    the win rate is 16% against ~40% at 0.50-0.60 — should be an explicit act,
    not a side effect of loosening a gate."""
    wide = {**GATE, "max_conf": 1.05}
    v = validate_entry(ctx(gate=wide, confidence=0.95))
    assert not v.ok
    assert any("anti-predictive" in r for r in v.reasons)


def test_the_band_can_be_entered_deliberately():
    wide = {**GATE, "max_conf": 1.05}
    assert validate_entry(ctx(gate=wide, confidence=0.95,
                              allow_anti_predictive=True)).ok


# ── Position and risk invariants ───────────────────────────────────────────

def test_no_stacking_onto_an_open_position():
    v = validate_entry(ctx(position_status="LONG"))
    assert not v.ok
    assert any("stack" in r for r in v.reasons)


def test_the_daily_loss_limit_stops_new_risk():
    v = validate_entry(ctx(day_pnl=-5200.0, daily_loss_limit=5000.0))
    assert not v.ok
    assert any("loss limit" in r for r in v.reasons)


def test_the_position_cap_is_respected():
    assert not validate_entry(ctx(open_positions=5, max_positions=5)).ok
    assert validate_entry(ctx(open_positions=4, max_positions=5)).ok


def test_a_symbol_outside_the_tradable_universe_is_blocked():
    """Renamed and delisted tickers 403 on the broker; a hardcoded universe goes
    stale silently."""
    v = validate_entry(ctx(tradable_universe={"TCS", "INFY"}))
    assert not v.ok
    assert any("not in the tradable universe" in r for r in v.reasons)


def test_a_closed_market_blocks_live_but_not_replay():
    assert not validate_entry(ctx(market_open=False, mode="paper")).ok
    # Replay and backtest are replaying a finished day — that is the point.
    assert validate_entry(ctx(market_open=False, mode="replay")).ok
    assert validate_entry(ctx(market_open=False, mode="backtest")).ok


# ── The two properties that matter most ────────────────────────────────────

def test_a_check_that_raises_blocks_rather_than_passing(monkeypatch):
    """Fail closed. An unusable validator must not wave a trade through."""
    import app.agents.validator as V

    def boom(_ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(V, "CHECKS", [("exploding", boom)])
    v = V.validate_entry(ctx())
    assert not v.ok
    assert any("refusing to assume it passed" in r for r in v.reasons)


def test_every_reason_is_collected_not_just_the_first():
    """A wrong decision is usually wrong in several ways; the log is more useful
    with all of them than with whichever check happens to run first."""
    v = validate_entry(ctx(price=0, score=10.0, position_status="LONG"))
    assert len(v.reasons) >= 3


def test_the_verdict_can_never_approve_anything():
    """The safety argument in one test: Verdict.ok is derived purely from the
    absence of reasons, so no code path can construct an approving verdict that
    had a failure in it."""
    assert Verdict(["something failed"], 8).ok is False
    assert bool(Verdict([], 8)) is True
    assert Verdict(["a", "b"], 8).as_reason() == "a; b"


# ── Independent recomputation ──────────────────────────────────────────────
#
# The checks above verify the numbers we were handed are self-consistent. This
# rebuilds the score from raw inputs and compares. It is the only check that can
# catch `_step` accumulating a WRONG score that is nonetheless consistent with
# itself — a double-count, a dropped branch, a flipped comparison.

from app.agents.validator import recompute_score

# The trend leg has two branches and the deployment picks one via
# NEURADEX_TREND_FILTER. These tests pin BOTH explicitly rather than inheriting
# whichever the ambient environment happens to set — the first run of this suite
# failed precisely because backend/.env carries `legacy` and the fixture assumed
# the inverted filter, which is a nice demonstration of why a test must not read
# its own configuration from the environment under test.
#
# WEAK tape: price under VWAP and SMA5 < SMA20. Scores 25 under the INVERTED
# filter (points go to weakness) and is a hard block under LEGACY (falling
# knife). STRONG tape is the exact mirror.
IND_WEAK = {"vwap": 1210.0, "sma5": 1200.0, "sma20": 1215.0, "rsi": 40.0}
IND_STRONG = {"vwap": 1200.0, "sma5": 1215.0, "sma20": 1200.0, "rsi": 60.0}


@pytest.fixture
def inverted(monkeypatch):
    """Force the post-2026-08-10 filter: points to weakness, strength blocks."""
    import app.services.sessions_service as S
    monkeypatch.setattr(S, "_TREND_FILTER_LEGACY", False)


@pytest.fixture
def legacy(monkeypatch):
    """Force the rollback filter: points to strength, counter-trend blocks."""
    import app.services.sessions_service as S
    monkeypatch.setattr(S, "_TREND_FILTER_LEGACY", True)
AGENTS_3BUY = [
    {"agent_name": "gbm", "action": "BUY"},           # reliable
    {"agent_name": "meanrev", "action": "BUY"},       # reliable
    {"agent_name": "technical", "action": "BUY"},
]


def rctx(**over):
    base = ctx(candle={"time": "10:15", "close": 1204.5, "volume": 48210},
               agents=AGENTS_3BUY, indicators=IND_WEAK,
               ens_action="BUY", tsig=0, buy_votes=3)
    base.update(over)
    return base


def test_recomputation_reproduces_the_documented_weights(inverted):
    """consensus 30 + co-sign 10 + trend 25 + rsi 25 + ensemble 10 = 100."""
    got = recompute_score(rctx())
    assert got is not None
    assert got["components"] == {
        "consensus": 30.0, "reliable_cosign": 10.0,
        "trend": 25.0, "rsi": 25.0, "ensemble": 10.0,
    }
    assert got["score"] == 100.0
    assert got["hard_block"] is False


def test_an_agreeing_score_passes(inverted):
    assert validate_entry(rctx(score=100.0)).ok


def test_a_score_the_recomputation_disagrees_with_is_blocked(inverted):
    """The core of the check: `_step` claims 95, the raw inputs say 100. One of
    the two is wrong and which is not knowable here, so the trade does not go."""
    v = validate_entry(rctx(score=95.0))
    assert not v.ok
    assert any("score disagreement" in r for r in v.reasons)


def test_the_disagreement_names_the_components(inverted):
    """A mismatch of exactly 13 should be identifiable as the VWAP leg without
    re-reading the scorer."""
    v = validate_entry(rctx(score=87.0))
    assert any("recomputed as" in r and "trend" in r for r in v.reasons)


def test_chasing_strength_recomputes_as_a_hard_block(inverted):
    """Price above VWAP with SMA5 > SMA20 is the measured worst cell (22.6% win
    vs 28.6%). If a decision reaches entry from that cell, the recomputation
    catches it even when the claimed score looks fine."""
    v = validate_entry(rctx(indicators=IND_STRONG, score=100.0))
    assert not v.ok
    assert any("HARD BLOCK" in r for r in v.reasons)


def test_the_legacy_filter_mirrors_it(legacy):
    """Under the rollback the polarity flips: strength scores, weakness blocks.
    Both branches have to be right, because NEURADEX_TREND_FILTER=legacy is the
    documented one-step rollback and backend/.env currently sets it."""
    strong = recompute_score(rctx(indicators=IND_STRONG))
    assert strong["hard_block"] is False
    assert strong["components"]["trend"] == 25.0

    weak = recompute_score(rctx(indicators=IND_WEAK))
    assert weak["hard_block"] is True


def test_thin_net_consensus_scores_lower(inverted):
    """3 BUY - 2 SELL = net 1 earns 15 rather than 30.

    The sellers here are deliberately NOT day_structure/meanrev (structural) or
    momentum/pattern/regime/volatility (measured anti-predictive as sellers) —
    those are excluded from the net count, so using them would have tested
    nothing. The first draft of this test used two of them and passed a 30.
    """
    agents = AGENTS_3BUY + [
        {"agent_name": "gbm_sell_proxy", "action": "SELL"},
        {"agent_name": "sentiment", "action": "SELL"},
    ]
    got = recompute_score(rctx(agents=agents))
    assert got["components"]["consensus"] == 15.0


def test_discounted_sellers_do_not_count_against_consensus(inverted):
    """The mirror of the above, so the carve-out itself is pinned: a wall of
    structural and anti-predictive SELLs leaves full consensus intact."""
    agents = AGENTS_3BUY + [
        {"agent_name": n, "action": "SELL"}
        for n in ("day_structure", "meanrev", "momentum", "pattern", "regime", "volatility")
    ]
    got = recompute_score(rctx(agents=agents))
    assert got["components"]["consensus"] == 30.0


def test_a_consensus_with_no_proven_voter_loses_the_cosign(inverted):
    agents = [{"agent_name": "technical", "action": "BUY"},
              {"agent_name": "momentum", "action": "BUY"}]
    got = recompute_score(rctx(agents=agents, buy_votes=2))
    assert got["components"]["reliable_cosign"] == 0.0
    assert got["components"]["consensus"] == 30.0        # still full consensus


def test_the_timing_bonus_is_capped_at_100(inverted):
    got = recompute_score(rctx(tsig=1))
    assert got["score"] == 100.0                          # 100 + 5, capped


def test_recomputation_abstains_when_raw_inputs_are_absent():
    """It must not guess. Without agents/indicators the other checks still run,
    but this one has nothing to verify against."""
    assert recompute_score(ctx()) is None
    assert validate_entry(ctx(score=86.0)).ok
