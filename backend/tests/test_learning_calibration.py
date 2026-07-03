"""Unit tests for the learning-loop calibration fixes (no network, no DB).

Locks in the three repairs:
  1. Action-aware vote correctness — a SELL/HOLD voter is right when the
     executed (long-only) trade LOST; the old logic inverted SELL.
  2. Lift-over-base action factor — skill is measured against the executed-trade
     base rate, not a fixed 0.5 (which halved every directional vote at a ~28%
     base win rate).
  3. Bayesian shrinkage — small samples pull toward the base rate so a lucky
     20-trade run can't earn a 2× factor.
"""
from app.agents.learning import _vote_was_correct, _shrunk_rate, _DECAY_PER_TRADE
from app.agents.ensemble import _action_factor


# ── 1. Action-aware correctness ───────────────────────────────────────────────

def test_buy_vote_correct_when_trade_won():
    assert _vote_was_correct("BUY", trade_won=True) is True
    assert _vote_was_correct("BUY", trade_won=False) is False


def test_sell_vote_correct_when_trade_lost():
    # The regression this suite exists for: good bears must score HIGH.
    assert _vote_was_correct("SELL", trade_won=False) is True
    assert _vote_was_correct("SELL", trade_won=True) is False


def test_hold_vote_correct_when_trade_lost():
    assert _vote_was_correct("HOLD", trade_won=False) is True
    assert _vote_was_correct("HOLD", trade_won=True) is False


# ── 2. Lift-over-base action factor ──────────────────────────────────────────

def test_factor_neutral_at_base_rate():
    # Scoring exactly the base rate = no skill signal = 1.0×.
    assert _action_factor(0.28, 0.28) == 1.0
    assert _action_factor(0.72, 0.72) == 1.0


def test_factor_rewards_lift_and_punishes_lag():
    assert abs(_action_factor(0.42, 0.28) - 1.5) < 1e-9   # 1.5× the base accuracy
    assert abs(_action_factor(0.14, 0.28) - 0.5) < 1e-9   # half the base accuracy


def test_factor_clamped():
    assert _action_factor(0.95, 0.10) == 2.0    # cap the upside
    assert _action_factor(0.01, 0.90) == 0.5    # floor the downside


def test_factor_neutral_without_data():
    # Cold start (no rate) or missing/zero base must not shift the vote.
    assert _action_factor(None, 0.28) == 1.0
    assert _action_factor(0.30, None) == 1.0
    assert _action_factor(0.30, 0.0) == 1.0


def test_old_formula_bias_is_gone():
    # Regression: at a 28% base win rate, the old `2*rate` formula scaled a
    # perfectly average BUY voter to 0.56× — a structural HOLD bias. Now: 1.0×.
    base = 0.28
    average_voter_rate = base
    assert _action_factor(average_voter_rate, base) == 1.0


# ── 3. Bayesian shrinkage ─────────────────────────────────────────────────────

def test_shrinkage_pulls_small_samples_to_base():
    # 20/20 correct is impressive but tiny — must NOT publish as 1.0.
    r = _shrunk_rate(correct=20, total=20, base=0.28)
    assert 0.60 < r < 0.70          # (20 + 20*0.28) / 40 = 0.64


def test_shrinkage_vanishes_with_volume():
    r = _shrunk_rate(correct=700, total=1000, base=0.28)
    assert abs(r - 0.70) < 0.01     # large n → essentially the raw rate


def test_shrinkage_empty_sample_is_base():
    assert _shrunk_rate(correct=0, total=0, base=0.28) == 0.28


# ── Weight decay sanity ───────────────────────────────────────────────────────

def test_decay_half_life_is_200_trades():
    w = 3.0
    for _ in range(200):
        w = 1.0 + (w - 1.0) * _DECAY_PER_TRADE
    # After one half-life the excess over 1.0 should have halved: 3.0 → ~2.0.
    assert abs(w - 2.0) < 0.01
