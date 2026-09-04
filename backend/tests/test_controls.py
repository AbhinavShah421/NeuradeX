"""Unit tests for the runtime control plane (no Redis, no DB).

These knobs decide whether a trade happens, and the page exposes them for
editing, so the validation is the safety barrier rather than a formality. The
sharp edge is `score_min`: the gate compares a 0-100 entry score against it, so
mistyping 78 as 7.8 does not tighten anything — it admits essentially every
setup on the next candle. That has to be rejected server-side, not in the form.
"""
import pytest

from app.services.controls import (
    control_specs, spec, validate,
)


def test_every_control_declares_what_it_needs_to_be_safe():
    for c in control_specs():
        assert c["id"] and c["label"] and c["group"], c
        assert c["type"] in ("number", "boolean", "enum"), c
        # A control with no default cannot be reset, which makes an edit
        # one-way. Booleans legitimately default to False.
        assert c.get("default") is not None or c["type"] == "boolean", c["id"]
        if c["type"] == "number":
            assert c.get("min") is not None and c.get("max") is not None, c["id"]
            assert c["min"] < c["max"], c["id"]
        if c["type"] == "enum":
            assert c.get("options"), c["id"]
            assert c["default"] in c["options"], c["id"]
        # Where the value is consumed — without it the page cannot tell you
        # what a knob actually affects.
        assert c.get("read_in"), c["id"]


def test_score_min_typo_is_rejected():
    ok, _, err = validate("gate.gentle.score_min", 7.8)
    assert not ok and "minimum" in err
    ok, val, _ = validate("gate.gentle.score_min", 78)
    assert ok and val == 78


def test_numbers_outside_bounds_are_rejected_both_ways():
    for cid, low, high in (
        ("gate.gentle.score_min", 39, 101),
        ("ensemble.dir_dominance", 0.9, 3.1),
        ("ensemble.dir_min_voters", 0, 6),
        ("gate.gentle.max_conf", -0.1, 1.06),
    ):
        assert not validate(cid, low)[0], f"{cid} accepted {low}"
        assert not validate(cid, high)[0], f"{cid} accepted {high}"


def test_integer_controls_reject_fractions():
    # dir_min_voters of 2.5 would truncate somewhere downstream and the panel
    # would show a value the engine is not using.
    ok, _, err = validate("ensemble.dir_min_voters", 2.5)
    assert not ok and "whole number" in err
    ok, val, _ = validate("ensemble.dir_min_voters", 3)
    assert ok and val == 3 and isinstance(val, int)


def test_enum_controls_reject_anything_off_the_list():
    assert not validate("ensemble.vote_mode", "aggressive")[0]
    assert validate("ensemble.vote_mode", "legacy")[0]
    assert not validate("gate.gentle.min_grade", "Z")[0]
    assert validate("gate.gentle.min_grade", "B")[0]


def test_booleans_accept_the_shapes_a_form_actually_sends():
    for raw, want in (("true", True), ("1", True), ("on", True),
                      ("false", False), ("0", False), (True, True), (False, False)):
        ok, val, _ = validate("gate.gentle.need_reliable", raw)
        assert ok and val is want, raw


def test_unknown_control_is_rejected():
    ok, _, err = validate("gate.gentle.definitely_not_a_control", 1)
    assert not ok and "unknown" in err


def test_memory_gate_allows_zero():
    # These bounds legitimately include 0/0.0, which is why the consumer must
    # not use `or` to fall back — that would silently restore the default.
    assert validate("ensemble.mem_min_samples", 0)[0]
    assert validate("ensemble.mem_gate_winrate", 0.0)[0]


def test_measured_dead_ends_carry_their_evidence():
    # A page that lets you change a knob without saying it was already measured
    # is worse than no page. These three have documented results.
    for cid in ("gate.gentle.max_conf", "gate.gentle.need_reliable",
                "ensemble.dir_dominance"):
        s = spec(cid)
        assert s and s.get("evidence"), f"{cid} has no evidence attached"


def test_dangerous_controls_are_labelled():
    for cid in ("gate.gentle.score_min", "ensemble.dir_min_voters",
                "ensemble.vote_mode"):
        s = spec(cid)
        assert s and s.get("danger"), f"{cid} carries no danger note"
