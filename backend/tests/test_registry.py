"""The register is only binding if the build enforces it.

`test_every_live_behaviour_switch_is_registered` is the enforcement. It scans
live source for behaviour switches and fails when one has no register entry, so
a new knob cannot be added quietly and an existing one cannot drift out of view.

It deliberately does NOT assert that everything is justified — most of the
system currently runs on INCONCLUSIVE or UNMEASURED evidence, and a test that
demanded otherwise would either be permanently red or invite dishonest entries.
The register's job is to make the debt visible and countable.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.research.registry import BEHAVIOURS, evidence_debt, unjustified

# Prefixes that denote a deliberate behaviour switch rather than ordinary
# configuration (hosts, ports, credentials, model names).
_SWITCH = re.compile(r'getenv\(\s*["\'](NEURADEX_[A-Z0-9_]+|ENSEMBLE_[A-Z0-9_]+|AGGREGATE_[A-Z0-9_]+)["\']')

_REPO = Path(__file__).resolve().parents[2]
_LIVE_DIRS = ("backend/app", "ensemble-engine/app")


def _switches_in_source() -> dict[str, str]:
    """Every behaviour switch live code reads -> the first place it is read."""
    found: dict[str, str] = {}
    for d in _LIVE_DIRS:
        root = _REPO / d
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts or "/research/" in py.as_posix():
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for m in _SWITCH.finditer(line):
                    found.setdefault(m.group(1), f"{py.relative_to(_REPO)}:{line_no}")
    return found


def test_every_live_behaviour_switch_is_registered():
    """A knob that can change what the system trades must declare its evidence."""
    registered = {b.env for b in BEHAVIOURS}
    in_source = _switches_in_source()

    missing = sorted(set(in_source) - registered)
    assert not missing, (
        "These behaviour switches are read by live code but have no entry in "
        "app/research/registry.py:\n"
        + "\n".join(f"  {env}  ({in_source[env]})" for env in missing)
        + "\n\nAdd a Behaviour(...) declaring what evidence justifies the current "
          "default — 'UNMEASURED' is an acceptable and honest answer."
    )


def test_register_has_no_entries_for_switches_that_no_longer_exist():
    """Stale entries are worse than none: they imply a control that is gone."""
    in_source = set(_switches_in_source())
    # Switches read only outside the scanned dirs are still legitimate; only
    # flag entries whose recorded location no longer contains the name.
    stale = []
    for b in BEHAVIOURS:
        if b.env in in_source:
            continue
        path = _REPO / b.read_in.split(":")[0]
        if not path.exists() or b.env not in path.read_text(encoding="utf-8", errors="ignore"):
            stale.append(b.env)
    assert not stale, f"register entries no longer found in the code they cite: {stale}"


def test_entries_are_internally_consistent():
    for b in BEHAVIOURS:
        assert b.evidence.strip(), f"{b.env} has no evidence text"
        assert b.read_in.strip(), f"{b.env} does not say where it is read"
        if b.status in ("PASS", "INCONCLUSIVE"):
            assert b.measured_on is not None, (
                f"{b.env} claims status {b.status} but records no measurement date"
            )
        if not b.affects_trades:
            assert b.status == "NEUTRAL", (
                f"{b.env} does not affect trades, so its status should be NEUTRAL"
            )
        assert b.justified == (b.status in ("PASS", "NEUTRAL"))


def test_evidence_debt_counts_what_is_actually_unproven():
    debt = evidence_debt()
    assert debt["total"] == len(BEHAVIOURS)
    assert debt["affects_trades"] == sum(1 for b in BEHAVIOURS if b.affects_trades)
    assert debt["unjustified"] == [b.env for b in unjustified()]
    # Every trade-affecting behaviour lands in exactly one bucket.
    assert (debt["passed"] + debt["inconclusive"] + debt["unmeasured"]
            == debt["affects_trades"])


def test_nothing_claims_a_gate_pass_it_has_not_earned():
    """No behaviour may be marked PASS while the gate still returns INCONCLUSIVE.

    As of 2026-08-18 nothing has cleared the 60-day floor, so any PASS here would
    be a claim the data cannot support. Remove this test the day one legitimately
    passes.
    """
    passed = [b.env for b in BEHAVIOURS if b.status == "PASS"]
    assert not passed, (
        f"{passed} claim a gate PASS, but no candidate has cleared the 60-day "
        "floor yet. Re-run app.research.feature_survey before marking anything PASS."
    )
