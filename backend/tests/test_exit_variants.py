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
    _simulate_policy, _simulate_long, _aggregate_ab_rows,
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


# ── A/B aggregation fairness ─────────────────────────────────────────────────
# The bug this section locks out: exit_ab_report summed every row per variant,
# so a variant added later was scored only on the days it happened to exist for
# (n ranged 2,611–30,479 across variants) and the two entry populations were
# pooled despite very different base rates. Every exit-policy adoption to date
# was decided on that table.

def _row(day, variant, source, n, wins, avg):
    return (day, variant, source, n, wins, avg)


def test_days_missing_a_variant_are_excluded():
    # d1 has both variants; d2 was evaluated before "new" existed.
    daily = [
        _row("d1", "old", "sessions", 100, 50, 1.0),
        _row("d1", "new", "sessions", 100, 10, -1.0),
        _row("d2", "old", "sessions", 100, 90, 5.0),
    ]
    summary, coverage = _aggregate_ab_rows(daily)
    assert coverage["sessions"]["common_days"] == ["d1"]
    assert coverage["sessions"]["excluded_days"] == ["d2"]
    # "old" must NOT get credit for d2, where "new" never ran.
    old = next(r for r in summary if r["variant"] == "old")
    assert old["avg_pnl_pct"] == 1.0
    assert old["n"] == 100


def test_every_variant_compared_on_equal_sample():
    daily = [
        _row("d1", "a", "sessions", 100, 50, 1.0),
        _row("d1", "b", "sessions", 100, 25, 2.0),
        _row("d2", "a", "sessions", 200, 50, 1.0),
        _row("d2", "b", "sessions", 200, 25, 2.0),
    ]
    summary, _ = _aggregate_ab_rows(daily)
    assert len({r["n"] for r in summary}) == 1, "variants must share one sample size"


def test_populations_are_never_pooled():
    # Same variant, both sources — must stay two rows, not one merged number.
    daily = [
        _row("d1", "a", "sessions", 100, 10, -1.0),
        _row("d1", "a", "store", 100, 90, 5.0),
    ]
    summary, coverage = _aggregate_ab_rows(daily)
    assert {r["source"] for r in summary} == {"sessions", "store"}
    assert len(summary) == 2
    by_src = {r["source"]: r["avg_pnl_pct"] for r in summary}
    assert by_src["sessions"] == -1.0 and by_src["store"] == 5.0


def test_live_policy_is_flagged():
    live_name = next(k for k, v in EXIT_VARIANTS.items() if v == LIVE_POLICY)
    daily = [_row("d1", live_name, "sessions", 10, 5, 0.0),
             _row("d1", "baseline", "sessions", 10, 5, 0.0)]
    summary, _ = _aggregate_ab_rows(daily)
    assert next(r for r in summary if r["variant"] == live_name)["is_live"]
    assert not next(r for r in summary if r["variant"] == "baseline")["is_live"]


def test_summary_sorted_best_first_within_source():
    daily = [
        _row("d1", "worse", "sessions", 100, 10, -2.0),
        _row("d1", "better", "sessions", 100, 50, -1.0),
    ]
    summary, _ = _aggregate_ab_rows(daily)
    assert [r["variant"] for r in summary] == ["better", "worse"]


# ── Entry cutoff parity (CF labels vs live gates) ────────────────────────────
# The bug this section locks out: the CF labeler had no entry cutoff, so 43.4%
# of labels were bars the live gates would never open on. Past the 14:45
# square-off a simulated entry exits on the SAME bar for exactly the round-trip
# cost — near-deterministic small losses that carried no signal about the exit
# rules, yet made up 43% of the exit-policy A/B sample.

def test_entry_cutoff_matches_live_sessions():
    # counterfactual.py cannot import sessions_service (circular), so the two
    # constants are pinned together here instead.
    from app.agents.counterfactual import _ENTRY_CUTOFF_MIN
    assert _ENTRY_CUTOFF_MIN == _LATE_ENTRY_CUTOFF_MIN


def test_tradeable_entry_predicate():
    from app.agents.counterfactual import _is_tradeable_entry
    assert _is_tradeable_entry("09:15")
    assert _is_tradeable_entry("12:59")
    # The live gate refuses an entry AT the cutoff minute, not just after it.
    assert not _is_tradeable_entry("13:00")
    assert not _is_tradeable_entry("14:50")   # past square-off: same-bar exit
    assert not _is_tradeable_entry("garbage")


def test_store_population_uses_the_same_cutoff():
    # The store A/B used 13:30 while live was 13:00. That half-hour gap is one
    # reason the two populations ranked the exit variants differently.
    from app.agents.counterfactual import _AB_STORE_LAST_ENTRY_MIN, _ENTRY_CUTOFF_MIN
    assert _AB_STORE_LAST_ENTRY_MIN == _ENTRY_CUTOFF_MIN


def test_tradeable_sql_filter_is_chronological():
    # candle_time is TEXT 'HH:MM', so the SQL filter relies on zero-padded
    # lexicographic ordering being chronological ordering.
    from app.agents.counterfactual import _ENTRY_CUTOFF_HHMM
    assert _ENTRY_CUTOFF_HHMM == "13:00"
    assert "09:15" < _ENTRY_CUTOFF_HHMM
    assert not ("13:38" < _ENTRY_CUTOFF_HHMM)
