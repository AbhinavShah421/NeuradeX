"""Unit tests for the exit-policy A/B engine and the gate quick-wins (no DB).

Locks in:
  • _simulate_policy(baseline) ≡ the live _tech_signal LONG rules (parity with
    the pre-refactor _simulate_long behaviour).
  • The variant knobs actually change behaviour in the intended direction
    (grace period survives an early dip that stops the baseline out; hold60
    stays in longer than hold_cap 30).
  • The forensics-driven gate constants (conf ceiling under the 0.70 cliff,
    pattern/sentiment-only reliable set, 13:30 entry cutoff).
"""
from app.agents.counterfactual import (
    BASELINE_POLICY, EXIT_VARIANTS, _day_indicators, _simulate_policy, _simulate_long,
)
from app.services.sessions_service import (
    TRADE_GATES, _RELIABLE_BUY_AGENTS, _LATE_ENTRY_CUTOFF_MIN, _PAPER_CONFIG_DEFAULT,
)


def _bar(hhmm: str, o: float, h: float, l: float, c: float) -> dict:
    return {"time": hhmm, "open": o, "high": h, "low": l, "close": c, "volume": 0}


def _mins(i: int, start_h: int = 10) -> str:
    return f"{start_h + i // 60:02d}:{i % 60:02d}"


def _flat(n: int = 60, px: float = 100.0) -> list[dict]:
    return [_bar(_mins(i), px, px, px, px) for i in range(n)]


# ── Baseline parity ───────────────────────────────────────────────────────────

def test_simulate_long_equals_policy_baseline():
    # A wavy day: dip then recovery — exercises stop/cut paths.
    bars = []
    px = 100.0
    for i in range(50):
        o = px
        px = round(px * (0.998 if i < 8 else 1.001), 4)
        bars.append(_bar(_mins(i), o, max(o, px), min(o, px), px))
    inds = _day_indicators(bars)
    for entry in (1, 5, 12):
        assert _simulate_long(bars, entry) == _simulate_policy(bars, inds, entry, BASELINE_POLICY)


# ── Variant behaviour ─────────────────────────────────────────────────────────

def test_grace_period_survives_early_noise_dip():
    # -1.2% dip in the first 6 minutes, then +3% recovery. Baseline (stop -1%,
    # no grace) is stopped out negative; wide_stop (grace 10, stop -1.5%) should
    # ride through the dip and exit meaningfully better.
    bars = []
    px = 100.0
    for i in range(45):
        o = px
        if i < 6:
            px = round(px * 0.998, 4)     # ~-1.2% by bar 6
        else:
            px = round(px * 1.0015, 4)    # steady recovery, ~+3% over the rest
        bars.append(_bar(_mins(i), o, max(o, px), min(o, px), px))
    inds = _day_indicators(bars)
    base = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["baseline"])
    wide = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["wide_stop"])
    assert base is not None and wide is not None
    assert wide > base                     # grace + wider stop rides the recovery
    assert base < 0                        # baseline crystallised the noise dip


def test_hold60_stays_in_longer_than_cap30():
    # Slow steady climb (+0.05%/bar) that never hits stop/take/cut: both exit at
    # their hold caps, so the 60-min variant banks about twice the move.
    bars = []
    px = 100.0
    for i in range(80):
        o = px
        px = round(px * 1.0005, 4)
        bars.append(_bar(_mins(i), o, px, o, px))
    inds = _day_indicators(bars)
    p30 = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["baseline"])
    p60 = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["hold60"])
    assert p30 is not None and p60 is not None
    assert p60 > p30 > 0


def test_all_variants_run_on_flat_day():
    bars = _flat(70)
    inds = _day_indicators(bars)
    for name, policy in EXIT_VARIANTS.items():
        pnl = _simulate_policy(bars, inds, 1, policy)
        assert pnl is not None, name
        assert -1.0 < pnl < 0.0, name      # flat market: costs only, all variants


# ── Gate quick-wins (forensics-locked constants) ──────────────────────────────

def test_conf_ceilings_under_the_070_cliff():
    # Win-rate cliff at conf 0.70 (34.6% below, 25.3% above — 12.6k trades).
    assert TRADE_GATES["strict"]["max_conf"] <= 0.70
    assert TRADE_GATES["gentle"]["max_conf"] <= 0.70


def test_reliable_set_is_evidence_based():
    # pattern 48.2% / sentiment 44.1% are the only positive-EV BUY voters;
    # memory (21.8%) and gbm (base-rate) must be out.
    assert _RELIABLE_BUY_AGENTS == {"sentiment", "pattern"}


def test_gentle_gate_requires_reliable_voter():
    assert TRADE_GATES["gentle"]["need_reliable"] is True


def test_entry_cutoffs_at_1330():
    assert _LATE_ENTRY_CUTOFF_MIN == 13 * 60 + 30
    assert _PAPER_CONFIG_DEFAULT["no_entry_after"] == "13:30"
