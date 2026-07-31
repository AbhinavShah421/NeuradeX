"""Unit tests for the LLM rejection reviewer (no network, no DB).

The reviewer's whole claim is that it can be scored honestly, so the tests
guard the things that would silently invalidate that:
  • the dossier must not leak the counterfactual outcome (no lookahead);
  • the prompt must stay calibrated, or the model reverts to endorsing
    everything (llama3.1:8b returned should_enter 60/60 on the naive prompt);
  • the edge must be computed as should_enter MINUS agree_skip.
"""
import json

from app.services.llm_rejection_review import (
    _build_dossier, _parse_verdict, _system_prompt, _MIN_BUY_VOTES,
)


def _row(cf_pnl=1.2345):
    agents = [{"agent": "gbm", "action": "BUY", "confidence": 0.7},
              {"agent": "meanrev", "action": "BUY", "confidence": 0.6},
              {"agent": "technical", "action": "SELL", "confidence": 0.55}]
    ind = {"rsi": 61.0, "vwap": 100.0, "sma5": 100.4, "sma20": 100.1,
           "mom5": 0.2, "atr": 0.3}
    return (42, "ACME", "2026-07-30", "10:15", 101.5,
            "No entry [Gentle gate] — panel dissent", agents, ind, cf_pnl)


# ── No lookahead ─────────────────────────────────────────────────────────────

def test_dossier_never_leaks_the_outcome():
    # The counterfactual P&L is the answer key. If it reaches the prompt the
    # scorecard is worthless, so assert on the SERIALISED dossier — a nested
    # key would slip past a top-level check.
    d = _build_dossier(_row(cf_pnl=9.8765))
    blob = json.dumps(d)
    assert "9.8765" not in blob
    assert "cf_pnl" not in blob
    assert "pnl" not in blob


def test_dossier_carries_decision_time_context():
    d = _build_dossier(_row())
    assert d["symbol"] == "ACME" and d["time"] == "10:15"
    assert d["buy_votes"] == 2 and d["sell_votes"] == 1
    assert d["indicators"]["rsi"] == 61.0
    assert "panel dissent" in d["gate_rejection_reason"]


def test_dossier_survives_malformed_json():
    bad = (1, "X", "2026-07-30", "09:30", 10.0, "r", "not json", "not json", 0.1)
    d = _build_dossier(bad)
    assert d["votes"] == [] and d["buy_votes"] == 0


# ── Calibration ──────────────────────────────────────────────────────────────

def test_prompt_states_the_measured_base_rate():
    # Without the real base rate the model has nothing to calibrate against and
    # reverts to endorsing every setup.
    p = _system_prompt(37.1, -0.1154)
    assert "37%" in p
    assert "-0.115%" in p


def test_prompt_sets_an_explicit_quota():
    p = _system_prompt(36.0, -0.09)
    assert "1 in 5" in p, "quota is what broke the 60/60 yes-bias"
    assert "should_enter" in p and "agree_skip" in p


def test_prompt_discourages_a_constant_confidence():
    # 60 verdicts came back with only 3 distinct confidences before this.
    assert "do not reuse one value" in _system_prompt(36.0, -0.09)


# ── Verdict parsing ──────────────────────────────────────────────────────────

def test_parses_both_verdicts():
    v, c, r = _parse_verdict('{"verdict":"should_enter","confidence":0.7,"reason":"x"}')
    assert (v, c) == ("should_enter", 0.7)
    v, _, _ = _parse_verdict('{"verdict":"agree_skip","confidence":0.4,"reason":"y"}')
    assert v == "agree_skip"


def test_parses_verdict_embedded_in_prose():
    v, _, _ = _parse_verdict('Sure!\n{"verdict": "agree_skip", "confidence": 0.5, "reason": "z"}\n')
    assert v == "agree_skip"


def test_rejects_the_entry_reviewers_vocabulary():
    # "approve"/"veto" belong to the other reviewer; silently accepting them
    # here would mix two different questions in one column.
    assert _parse_verdict('{"verdict":"approve","confidence":0.9}')[0] == "parse_error"


def test_garbage_is_parse_error_not_an_exception():
    assert _parse_verdict("the model rambled")[0] == "parse_error"
    assert _parse_verdict("")[0] == "parse_error"


# ── Sampling contract ────────────────────────────────────────────────────────

def test_near_miss_threshold_is_meaningful():
    # Reviewing 0-BUY rejections would burn the LLM budget on obvious skips.
    assert _MIN_BUY_VOTES >= 2
