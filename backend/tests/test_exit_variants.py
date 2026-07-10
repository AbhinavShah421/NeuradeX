"""Unit tests for the exit-policy A/B engine and the gate quick-wins (no DB).

Locks in:
  • _simulate_policy(baseline) ≡ the live _tech_signal LONG rules (parity with
    the pre-refactor _simulate_long behaviour).
  • The variant knobs actually change behaviour in the intended direction
    (grace period survives an early dip that stops the baseline out; hold60
    stays in longer than hold_cap 30).
  • The forensics-driven gate constants (conf ceiling under the 0.70 cliff,
    evidence-based reliable-voter set, 13:00 entry cutoff).
"""
from app.agents.counterfactual import (
    BASELINE_POLICY, LIVE_POLICY, EXIT_VARIANTS, _day_indicators,
    _simulate_policy, _simulate_long,
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


# ── Live-policy parity ────────────────────────────────────────────────────────

def test_simulate_long_equals_live_policy():
    # CF labels must reflect what the live sessions would actually do —
    # _simulate_long ≡ _simulate_policy(LIVE_POLICY). Wavy day: dip then recovery.
    bars = []
    px = 100.0
    for i in range(50):
        o = px
        px = round(px * (0.998 if i < 8 else 1.001), 4)
        bars.append(_bar(_mins(i), o, max(o, px), min(o, px), px))
    inds = _day_indicators(bars)
    for entry in (1, 5, 12):
        assert _simulate_long(bars, entry) == _simulate_policy(bars, inds, entry, LIVE_POLICY)


def test_live_policy_is_the_ab_winner():
    # The live policy must equal the A/B-winning variant — if someone tweaks
    # one side, this forces them to reconcile both. Live since 2026-07-10:
    # lock08 + "let winners run" (trend-extended hold cap, mom-confirmed lock),
    # adopted after the Jul 8-10 post-exit audit (cap exits left +0.51% avg on
    # the table with the trend intact).
    assert LIVE_POLICY == EXIT_VARIANTS["wide_hold60_lock08_run"]
    # ...and the "baseline" variant stays the OLD tight policy for continuity.
    assert EXIT_VARIANTS["baseline"] == BASELINE_POLICY
    assert BASELINE_POLICY["stop_floor"] == 1.0 and BASELINE_POLICY["fast_cut"] is True


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


def test_trend_extended_cap_rides_a_steady_winner():
    # +0.02%/bar monotone climb: the flat 60-min cap banks ~1.2%, the
    # trend-extended cap rides the intact uptrend to the 2× hard cap (~2.4%).
    bars = []
    px = 100.0
    for i in range(130):
        o = px
        px = round(px * 1.0002, 4)
        bars.append(_bar(_mins(i), o, px, o, px))
    inds = _day_indicators(bars)
    capped   = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["wide_hold60_lock08"])
    extended = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["wide_hold60_lock08_run"])
    assert capped is not None and extended is not None
    assert extended > capped > 0


def test_mom_confirmed_lock_survives_one_bar_pause():
    # Rising stock takes a single -0.4% breather below SMA5 with 5-bar momentum
    # still positive, then keeps climbing. The bare lock cashes out on the
    # pause bar; the mom5<0-confirmed lock stays in and banks the larger move.
    bars = []
    px = 100.0
    for i in range(41):
        o = px
        if i <= 14:
            px = round(px * 1.0015, 4)     # steady climb, lock armed (>0.8%)
        elif i == 15:
            px = round(px * 0.9965, 4)     # one-bar dip under SMA5, mom5 > 0
        else:
            px = round(px * 1.0005, 4)     # climb resumes
        bars.append(_bar(_mins(i), o, max(o, px), min(o, px), px))
    inds = _day_indicators(bars)
    # Sanity: the dip bar is below SMA5 with mom5 still positive — the exact
    # situation the confirmation targets.
    assert bars[15]["close"] < inds[15]["sma5"]
    assert inds[15]["mom5"] > 0
    bare      = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["wide_hold60_lock08"])
    confirmed = _simulate_policy(bars, inds, 1, EXIT_VARIANTS["wide_hold60_lock08_run"])
    assert bare is not None and confirmed is not None
    assert confirmed > bare > 0


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
    # pattern 48.2% / sentiment 44.1% from the 12.6k-trade forensics; rl (65%
    # hit / +0.39% avg-30m) and meanrev (60% / +0.17%) added 2026-07-07 from the
    # forward-return audit of that day's live bars. memory (21.8%) and gbm
    # (base-rate) stay out — their BUYs showed no edge at scale.
    assert _RELIABLE_BUY_AGENTS == {"sentiment", "pattern", "rl", "meanrev"}


def test_gentle_gate_requires_reliable_voter():
    assert TRADE_GATES["gentle"]["need_reliable"] is True


def test_entry_cutoffs_at_1300():
    # 13:30 → 13:00 (2026-07-08): 13:00-13:30 entries went 0/8 on CF labels.
    assert _LATE_ENTRY_CUTOFF_MIN == 13 * 60


def test_paper_times_default_auto_and_resolve():
    # Defaults are "auto" (2026-07-10): the system picks the times from its own
    # forensics; a manually saved HH:MM applies as-is.
    from app.services.sessions_service import _resolve_paper_minutes
    assert _PAPER_CONFIG_DEFAULT == {"no_entry_after": "auto", "squareoff_after": "auto"}
    assert _resolve_paper_minutes("auto", 780) == 780
    assert _resolve_paper_minutes("Auto", 780) == 780
    assert _resolve_paper_minutes("14:00", 780) == 14 * 60
    assert _resolve_paper_minutes("", 885) == 885          # empty → auto
    assert _resolve_paper_minutes("garbage", 885) == 885   # unparsable → auto
