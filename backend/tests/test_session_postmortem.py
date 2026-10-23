"""Unit tests for the per-session post-mortem (no DB, no network).

Locks in the two things that are easy to get quietly wrong here:

  • The setup taxonomy must be TOTAL. The first cut classified strength only
    when momentum was also high, so `sma5>sma20 AND price>vwap AND mom<=0.15`
    fell through every branch into `no_clear_setup` — which meant the largest
    bucket in the corpus (41% at the time) was absorbing the exact cell the
    day-clustered study had flagged as harmful, and `no_clear_setup` carries a
    measured POSITIVE edge. A trade in the worst cell was being shown to the
    reader as mildly reassuring.

  • A culprit verdict needs a Bonferroni-clearing t. Twelve agents are tested at
    once; the single-test 1.96 would manufacture a culprit roughly half the time
    from noise alone.
"""
from app.services.session_postmortem import (
    SETUP_EDGE_PP, SETUP_ESTABLISHED, SETUP_TAGS,
    _loss_reason, _verdict, classify_setup,
)


def _ind(**over):
    d = {"rsi": 55.0, "sma5": 100.5, "sma20": 100.0, "vwap": 100.2,
         "momentum_pct": 0.0, "atr": 1.0}
    d.update(over)
    return d


def test_strength_without_momentum_is_not_no_clear_setup():
    # The regression. sma5>sma20 and price>vwap is the buying-strength cell
    # (-0.0815pp, t=-3.18 day-clustered); low momentum must not launder it into
    # the catch-all.
    tag = classify_setup(_ind(momentum_pct=-0.03), price=100.4)
    assert tag == "strength_drift"
    assert SETUP_EDGE_PP[tag] < 0
    assert tag in SETUP_ESTABLISHED


def test_strength_with_momentum_keeps_its_measured_identity():
    # The high-momentum subset must stay its own tag, or the -0.069pp/t=-3.18
    # measurement no longer refers to what the code computes.
    assert classify_setup(_ind(momentum_pct=0.5), price=100.4) == "momentum_into_resistance"


def test_every_tag_the_classifier_emits_has_a_measured_edge():
    # A tag with no entry in SETUP_EDGE_PP renders as a blank number in the UI.
    grid = [
        _ind(rsi=85.0), _ind(rsi=12.0),
        _ind(momentum_pct=0.5), _ind(momentum_pct=-0.03),
        _ind(sma5=99.0, sma20=100.0, vwap=100.2),
        _ind(sma5=99.0, sma20=100.0, vwap=98.0, momentum_pct=-0.5),
        _ind(sma5=99.0, sma20=100.0, vwap=98.0, momentum_pct=0.0, atr=0.001),
        _ind(rsi=None),
    ]
    seen = set()
    for ind in grid:
        for price in (100.4, 98.5):
            tag = classify_setup(ind, price)
            assert tag in SETUP_TAGS, tag
            assert tag in SETUP_EDGE_PP, f"{tag} has no measured edge"
            seen.add(tag)
    assert len(seen) >= 6, f"grid only reached {seen}"


def test_classify_setup_never_raises_on_junk():
    # It runs over historical rows whose indicator payloads vary.
    for bad in (None, {}, {"rsi": "n/a"}, {"rsi": 50, "sma5": None}):
        assert classify_setup(bad, price=100.0) in SETUP_TAGS
    assert classify_setup(_ind(), price=None) == "no_clear_setup"
    assert classify_setup(_ind(), price=0) == "no_clear_setup"


def test_only_a_bonferroni_clearing_t_names_a_culprit():
    # 12 agents tested at once -> |t| >= 3.0, not 1.96.
    assert _verdict(0.02, 3.5) == "culprit"
    assert _verdict(-0.02, -3.5) == "protective"
    assert _verdict(0.014, 1.83) == "leans culprit (not significant)"
    assert _verdict(-0.037, -2.93) == "leans protective (not significant)"
    assert _verdict(0.0, 0.4) == "no signal"
    # An agent with no computable t (never votes BUY) is not a culprit.
    assert _verdict(0.0, None) == "no signal"


def _report():
    return {
        "session_id": "abc", "symbol": "ACME", "date": "2026-09-04",
        "n_trades": 2, "n_losses": 2,
        "trades": [
            {"is_loss": True, "pnl_pct": -0.9, "setup": "strength_drift",
             "setup_edge_pp": -0.072, "setup_established": True,
             "loss_reason": "stopped out", "voted_buy": ["technical"],
             "entry_price": 101.5, "exit_price": 99.2, "indicators": {"rsi": 60}},
        ],
        "agent_attribution": [
            {"agent": "technical", "verdict": "leans culprit (not significant)",
             "baseline_lift": 0.0136, "baseline_t": 1.83},
            {"agent": "memory", "verdict": "protective",
             "baseline_lift": -0.0186, "baseline_t": -3.48},
            {"agent": "rl", "verdict": "no signal",
             "baseline_lift": -0.0017, "baseline_t": -0.42},
        ],
        "culprit_verdict": "No agent is a statistically established culprit.",
    }


def test_narrative_facts_state_significance_rather_than_implying_it():
    # Asked to judge significance from a raw t, the 8B got it backwards —
    # calling memory (t=-3.48, clears correction) "not significant". The flag
    # is now supplied, so the model never has to infer it.
    from app.services.session_postmortem import _narrative_facts

    verdicts = {v["agent"]: v for v in _narrative_facts(_report())["agent_culpability_verdicts"]}
    assert verdicts["memory"]["is_statistically_established"] is True
    assert verdicts["technical"]["is_statistically_established"] is False
    # "no signal" agents are not worth prose at all.
    assert "rl" not in verdicts


def test_narrative_facts_define_every_term_they_use():
    # Left to infer, the model read "protective" as "made this session's losses
    # less severe" — a causal claim about individual trades from a corpus-wide
    # rate difference. Every loaded term must ship with its meaning.
    from app.services.session_postmortem import _narrative_facts

    facts = _narrative_facts(_report())
    g = facts["glossary"]
    assert "verdict 'protective'" in g and "verdict 'culprit'" in g
    assert "does NOT mean" in g["verdict 'protective'"]
    # A positive edge means "lost less", and the glossary has to say so — every
    # absolute cell is negative and already net of costs.
    assert "never means profitable" in g["setup_edge_vs_other_entries_pp"]
    assert "corpus" in facts["context_every_reader_needs"]


def test_narrative_facts_withhold_raw_prices_the_model_could_recompute():
    # The model is told never to compute a number. Not handing it entry/exit
    # prices removes the temptation and the possibility.
    import json as _json
    from app.services.session_postmortem import _narrative_facts

    blob = _json.dumps(_narrative_facts(_report()))
    assert "101.5" not in blob and "99.2" not in blob
    for t in _narrative_facts(_report())["trades"]:
        assert "entry_price" not in t and "exit_price" not in t


def test_loss_reason_reports_the_mechanism_not_a_theory():
    assert "stopped out" in _loss_reason("stop_loss", -1.5, 20)
    assert "stagnation" in _loss_reason("stagnation", -0.3, 60)
    assert "square-off" in _loss_reason("eod_squareoff", -0.2, 300)
    assert _loss_reason(None, -0.2, 10) == "exit reason not recorded"
    assert _loss_reason("unknown", -0.2, 10) == "exit reason not recorded"
    # An unrecognised reason is passed through, not swallowed.
    assert _loss_reason("ensemble_sell", -0.2, 10) == "ensemble_sell"
