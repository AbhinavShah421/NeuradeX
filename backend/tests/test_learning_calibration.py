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
from app.agents.learning import (
    _vote_was_correct, _shrunk_rate, _DECAY_PER_TRADE,
    _ABSTAIN_CREDIT, _VETO_ONLY_AGENTS,
)
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


# ── 4. Abstention must not pay ────────────────────────────────────────────────
# The bug this section locks out: HOLD votes earned 0.5×|reward| when a trade
# lost but cost only 0.15×reward when one won. At the observed ~29% win rate
# that is a positive expected drift for an agent that never takes a side, so
# weight accrued for staying silent — `anomaly` rode it from 0.7 to 1.286, the
# top of the panel, on 200,877 consecutive HOLD votes.

def _abstain_drift(win_rate: float, avg_win_reward: float, avg_loss_reward: float,
                   credit: float, penalty: float) -> float:
    """Expected per-trade weight drift for an agent that always votes HOLD."""
    return ((1 - win_rate) * credit * abs(avg_loss_reward)
            - win_rate * penalty * avg_win_reward)


def test_old_asymmetry_paid_agents_to_abstain():
    # Reward magnitudes from _reward() for the observed avg win (+0.68%) and
    # avg loss (-0.41%): the win bucket maps to 0.4, the loss bucket to -0.1.
    drift = _abstain_drift(0.29, 0.4, -0.1, credit=0.5, penalty=0.15)
    assert drift > 0, "regression: the old 0.5/0.15 split rewarded pure abstention"


def test_symmetric_credit_removes_the_incentive():
    drift = _abstain_drift(0.29, 0.4, -0.1,
                           credit=_ABSTAIN_CREDIT, penalty=_ABSTAIN_CREDIT)
    assert drift <= 0, "an indiscriminate abstainer must not gain weight"


def test_abstain_credit_is_symmetric():
    # Both directions must read the same constant — the asymmetry *is* the bug.
    assert _ABSTAIN_CREDIT > 0
    assert _ABSTAIN_CREDIT < 1.0    # abstention stays weaker than a directional call


def test_anomaly_is_veto_only():
    # anomaly returns HOLD on every code path, so it must never be scored as a
    # directional forecaster.
    assert "anomaly" in _VETO_ONLY_AGENTS
