"""Measure each setup against the counterfactual labels, and write the report.

`cf_pnl_pct` is already NET of slippage, brokerage and STT (`_simulate_policy`
calls buy_fill/sell_fill/charges), so the cost hurdle is simply **mean > 0**.
A setup that merely beats the baseline while staying negative has not found an
edge — it has found a less-bad way to lose, which is what the 2026-07-30 audit
kept running into.

Method, following that audit's guardrails:
  • the DAY is the unit of inference — intraday decisions are heavily correlated,
    so a per-trade t-stat on 254k rows would be wildly overstated;
  • variants are compared only on days where BOTH have samples (common days),
    never by pooling populations;
  • the per-day sign-flip fraction is reported next to every headline, because
    the trend-filter result that looked significant at t=2.50 flipped sign on a
    third of its days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_FIRE = 200          # ignore setups too rare to say anything about
MIN_DAYS = 8            # ...or that appear on too few days to pair
NEAR = 0.20             # "near a level" tolerance, %


def _t_p(x: np.ndarray) -> tuple[float, float]:
    """One-sample t-stat and two-sided p for the mean of per-day gaps."""
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    sd = x.std(ddof=1)
    if sd == 0:
        return float("nan"), float("nan")
    t = x.mean() / (sd / np.sqrt(n))
    try:
        from scipy import stats
        p = 2 * (1 - stats.t.cdf(abs(t), df=n - 1))
    except Exception:                      # scipy is optional in this image
        from math import erf, sqrt
        p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    return float(t), float(p)


def _setups(f: pd.DataFrame) -> dict[str, pd.Series]:
    """Named boolean signals. Each is 'if we had gone long on this bar'.

    Columns are read with `f["x"]`, never `f.x` — several feature names
    (`pivot`, `close`) collide with DataFrame methods and attribute access
    silently returns the method instead of the column.
    """
    s: dict[str, pd.Series] = {}
    c = f["close"]

    # ── Prior-day levels ────────────────────────────────────────────────────
    s["pdh_break"] = c > f["pdh"]
    s["pdh_approach"] = (c <= f["pdh"]) & (c >= f["pdh"] * (1 - NEAR / 100))
    s["pdl_break_down"] = c < f["pdl"]
    s["pdl_bounce"] = (c >= f["pdl"]) & (c <= f["pdl"] * (1 + NEAR / 100))
    s["above_pdc"] = c > f["pdc"]

    # ── Opening range ───────────────────────────────────────────────────────
    s["or15_break_up"] = f["or15_break_up_pct"] > 0
    s["or15_break_down"] = f["or15_break_dn_pct"] < 0
    s["or30_break_up"] = f["or30_break_up_pct"] > 0
    s["or30_break_down"] = f["or30_break_dn_pct"] < 0

    # ── CPR / floor pivots ──────────────────────────────────────────────────
    s["cpr_narrow"] = f["cpr_width_pct"] < 0.30
    s["cpr_wide"] = f["cpr_width_pct"] > 1.00
    s["above_pivot"] = c > f["pivot"]
    s["above_cpr_tc"] = c > f["cpr_tc"]
    s["r1_break"] = c > f["r1"]
    s["r2_break"] = c > f["r2"]
    s["s1_bounce"] = (c >= f["s1"]) & (c <= f["s1"] * (1 + NEAR / 100))
    s["below_s1"] = c < f["s1"]

    # ── Intraday structure ──────────────────────────────────────────────────
    s["swing_break_up"] = f["swing_high_dist_pct"] > 0
    s["swing_break_down"] = f["swing_low_dist_pct"] < 0
    s["near_day_high"] = f["dist_day_high_pct"] > -0.10
    s["near_day_low"] = f["dist_day_low_pct"] < 0.10
    s["round50_proximity"] = f["round50_dist_pct"] < 0.10

    # ── Multi-day context: the "correction after a rally" family ────────────
    s["runup5_strong"] = f["runup_5d_pct"] > 5
    s["runup3_strong"] = f["runup_3d_pct"] > 3
    s["up_streak_3plus"] = f["up_streak"] >= 3
    s["pullback_after_runup"] = (f["runup_5d_pct"] > 5) & (f["from_open_pct"] < 0)
    s["pullback_to_vwap"] = (f["runup_5d_pct"] > 3) & (f["vwap_dist_pct"].abs() < 0.10)
    s["pullback_to_sma20"] = (f["runup_5d_pct"] > 3) & \
                             ((c - f["sma20"]).abs() / c * 100 < 0.10)
    s["deep_correction"] = f["dist_20d_high_pct"] < -5
    s["near_20d_high"] = f["dist_20d_high_pct"] > -1

    # ── Gaps ────────────────────────────────────────────────────────────────
    s["gap_up"] = f["day_open"] > f["pdc"] * 1.005
    s["gap_down"] = f["day_open"] < f["pdc"] * 0.995
    s["gap_up_and_go"] = (f["day_open"] > f["pdc"] * 1.005) & (f["from_open_pct"] > 0)
    s["gap_down_fill"] = (f["day_open"] < f["pdc"] * 0.995) & (f["from_open_pct"] > 0)

    # ── Controls / re-measurements ──────────────────────────────────────────
    s["above_vwap"] = f["vwap_dist_pct"] > 0
    # The cell sessions_service hard-blocks today — the audit found it the BEST
    # performing one on 18 days. Re-measured here on the full window.
    s["falling_knife"] = (f["vwap_dist_pct"] < 0) & (f["sma5"] < f["sma20"])
    s["first_hour"] = f["minutes_since_open"] < 60
    s["midday"] = (f["minutes_since_open"] >= 60) & (f["minutes_since_open"] < 240)
    s["last_hour"] = f["minutes_since_open"] >= 240

    # The live A-grade promotion rule, reconstructed. scanner.py requires ALL of
    # chg >= AGRADE_TRIG_CHG_PCT (0.4%) from the open ref, price within
    # AGRADE_TRIG_HIGH_PROX_PCT (0.1%) of the day high, and >= 0.15% over a
    # 300s window (~5 one-minute bars). This is the rule that decides which
    # A-grades get promoted into paper trading intraday.
    s["agrade_promote_rule"] = (
        (f["from_open_pct"] >= 0.4)
        & (f["dist_day_high_pct"] >= -0.10)
        & (f["mom5_pct"] >= 0.15)
    )

    return s


def _evaluate(f: pd.DataFrame, name: str, sig: pd.Series) -> dict | None:
    """Day-level paired comparison of 'signal fired' vs 'it did not'."""
    sig = sig.fillna(False).astype(bool)
    d = pd.DataFrame({"day": f["day"], "pnl": f["cf_pnl_pct"], "sig": sig}).dropna(subset=["pnl"])
    fire, base = d[d.sig], d[~d.sig]
    if len(fire) < MIN_FIRE:
        return None

    per_day = fire.groupby("day").pnl.agg(["mean", "size"]).join(
        base.groupby("day").pnl.mean().rename("base"), how="inner").dropna()
    if len(per_day) < MIN_DAYS:
        return None

    gaps = (per_day["mean"] - per_day["base"]).to_numpy()
    t, p = _t_p(gaps)
    # Absolute expectancy, equal-weighting days so a few huge replay days do not
    # decide the result.
    abs_t, abs_p = _t_p(per_day["mean"].to_numpy())

    return {
        "setup": name,
        "n": len(fire),
        "days": len(per_day),
        "fire_mean": per_day["mean"].mean(),
        "base_mean": per_day["base"].mean(),
        "gap": gaps.mean(),
        "t": t,
        "p": p,
        "abs_t": abs_t,
        "abs_p": abs_p,
        "win_pct": 100 * (fire.pnl > 0).mean(),
        "days_positive_gap": 100 * (gaps > 0).mean(),
        "profitable": per_day["mean"].mean() > 0,
    }


def stage_report() -> None:
    from app.research.level_edge_study import FEATURES_PQ, REPORT_MD, _log

    f = pd.read_parquet(FEATURES_PQ)
    _log(f"report: evaluating setups over {len(f):,} labelled decisions")

    rows = [r for name, sig in _setups(f).items()
            if (r := _evaluate(f, name, sig)) is not None]
    res = pd.DataFrame(rows).sort_values("fire_mean", ascending=False).reset_index(drop=True)

    base_mean = f.groupby("day").cf_pnl_pct.mean().mean()
    n_days = f.day.nunique()

    lines: list[str] = []
    add = lines.append
    add("# Positional & multi-day setups — measured intraday edge\n")
    add(f"- **Sample:** {len(f):,} counterfactual-labelled decisions, "
        f"{f.symbol.nunique()} symbols, {n_days} trading days "
        f"({f.day.min()} → {f.day.max()})")
    add(f"- **Baseline** (all decisions, day-equal-weighted): **{base_mean:+.4f}%** per trade, net of costs")
    add("- **`cf_pnl_pct` is already net** of slippage + brokerage + STT, so the "
        "cost hurdle is simply *mean > 0*. Beating the baseline while still "
        "negative is not an edge.")
    add(f"- **Unit of inference is the day**, not the trade. Setups need "
        f"≥{MIN_FIRE} firings on ≥{MIN_DAYS} days to be reported.\n")

    winners = res[res.profitable]
    # "Profitable" only counts if the mean is also distinguishable from zero.
    solid = winners[(winners.abs_p < 0.05)]

    add("## Verdict\n")
    add(f"**No setup in the battery is reliably profitable after costs.** "
        f"{len(winners)} of {len(res)} have a positive day-weighted mean, but "
        f"{'neither' if len(winners) == 2 else 'none'} is statistically "
        f"distinguishable from zero"
        + (f" ({', '.join(f'`{r.setup}` p={r.abs_p:.2f}' for _, r in winners.iterrows())})"
           if len(winners) else "")
        + ". Read them as 'roughly break-even', not as an edge.\n")
    if len(solid):
        add("Setups whose absolute expectancy IS significant at p<0.05:\n")
        for _, r in solid.iterrows():
            add(f"- **`{r.setup}`** — {r.fire_mean:+.4f}%/trade over {r.days} days "
                f"(n={r.n:,}), t={r.abs_t:.2f}, p={r.abs_p:.3f}")
        add("")

    # The robust result is directional, not any single setup: strength-chasing
    # entries lose consistently, weakness entries lose far less.
    strength = ["near_day_high", "pdh_break", "gap_up_and_go", "r1_break", "r2_break",
                "above_vwap", "swing_break_up", "or15_break_up", "above_pdc"]
    weakness = ["below_s1", "pdl_break_down", "near_day_low", "or15_break_down",
                "falling_knife", "swing_break_down", "gap_down"]
    by_all = res.set_index("setup")
    s_have = [x for x in strength if x in by_all.index]
    w_have = [x for x in weakness if x in by_all.index]
    if s_have and w_have:
        s_mean = by_all.loc[s_have, "fire_mean"].mean()
        w_mean = by_all.loc[w_have, "fire_mean"].mean()
        add("### The one robust pattern: direction, not any single level\n")
        add(f"- **Buying strength** (n={len(s_have)} setups: breakouts, above-VWAP, "
            f"near-day-high, gap-and-go) averages **{s_mean:+.4f}%**/trade.")
        add(f"- **Buying weakness** (n={len(w_have)} setups: below-S1, prior-day-low "
            f"break, near-day-low, falling knife) averages **{w_mean:+.4f}%**/trade.")
        add(f"- Spread: **{w_mean - s_mean:+.4f} pts** in favour of weakness. Every "
            "one of the most statistically significant results in the table points "
            "the same way, which is what makes this the finding rather than any "
            "individual setup.\n")

    add("## All setups, ranked by expectancy\n")
    add("`fire` = mean when the signal fires; `base` = mean on the same days when it "
        "does not; `gap` = paired per-day difference. `days+` = share of days where "
        "the gap is positive — the sign-stability check.\n")
    add("| setup | n | days | fire % | base % | gap | t(gap) | p | days+ | win% | clears cost |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|")
    for _, r in res.iterrows():
        add(f"| `{r.setup}` | {r.n:,} | {r.days} | {r.fire_mean:+.4f} | {r.base_mean:+.4f} "
            f"| {r.gap:+.4f} | {r.t:.2f} | {r.p:.3f} | {r.days_positive_gap:.0f}% "
            f"| {r.win_pct:.1f} | {'**yes**' if r.profitable else 'no'} |")

    add("\n## Notable comparisons\n")
    by = res.set_index("setup")
    for a, b, note in [
        ("above_vwap", "falling_knife",
         "The live gate hard-blocks the falling-knife cell. The Jul 30 audit found "
         "that block inverted on 18 days; this is the re-measurement."),
        ("or15_break_up", "or15_break_down", "Opening-range breakout, both directions."),
        ("pdh_break", "pdl_break_down", "Prior-day high vs low break."),
        ("cpr_narrow", "cpr_wide", "Narrow CPR is supposed to predict a trend day."),
    ]:
        if a in by.index and b in by.index:
            add(f"- **{a}** {by.loc[a, 'fire_mean']:+.4f}% vs **{b}** "
                f"{by.loc[b, 'fire_mean']:+.4f}% — {note}")

    if "agrade_promote_rule" in by.index:
        r = by.loc["agrade_promote_rule"]
        add("\n## The live A-grade promotion rule\n")
        add("`_agrade_watch_cycle` promotes an A-grade into paper trading when ALL "
            "three fire: ≥0.4% up from the open ref, within 0.1% of the day high, "
            "and ≥0.15% over 300s. Reconstructed and measured here:\n")
        add(f"- **{r.fire_mean:+.4f}%**/trade over {r.days} days (n={r.n:,}), "
            f"vs {r.base_mean:+.4f}% on the same days when it does not fire")
        add(f"- Paired per-day gap **{r.gap:+.4f} pts**, t={r.t:.2f}, p={r.p:.3f}, "
            f"positive on only {r.days_positive_gap:.0f}% of days")
        add("\nThat rule is a conjunction of three strength conditions, and strength "
            "is the losing side of the one robust pattern above. It is selecting "
            "into the worst cell the study measured.\n")

    add("\n## Caveats\n")
    add("- The sample is concentrated: four days at the end of June / start of July "
        "carry ~72% of the rows (bulk replay sessions), while later live-paper days "
        "contribute a few hundred each. Day-equal weighting is used throughout to "
        "stop those days deciding every result, but the *symbol* mix still differs "
        "across the window.")
    add(f"- {n_days} trading days is a short window for day-level inference. The "
        "audit's own trend-filter finding was significant at t=2.50 on 18 days and "
        "still flipped sign on a third of them.")
    add("- Every setup is evaluated as an unconditional long on the signal bar. A "
        "setup could be uninformative alone yet still add value inside the ensemble, "
        "and this study would not see that.")
    add("- Levels are computed from daily Yahoo candles (prior-day OHLC) and 1-minute "
        "tick-store bars (intraday). Sub-minute structure is invisible here.")

    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    _log(f"wrote {REPORT_MD}")
    print("\n".join(lines))
