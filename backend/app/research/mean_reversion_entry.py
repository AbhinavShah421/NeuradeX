"""B2b — does entering on a genuine deviation pay, at the measured half-life?

B2 established that the universe is strongly mean-reverting (Hurst 0.358, OU
half-life median 17 min) but that holding longer never helps: the unconditional
long decays monotonically from -0.130% at 5 min to -0.259% at 180 min.

Those two facts are only compatible one way — mean reversion pays *conditional
on entering when price is stretched*, and an unconditional entry captures none
of it. Every setup tested so far was level-based (`below_s1`, `falling_knife`,
`pdl_break_down`): proxies for "stretched", never a graded measure of it.

This sweeps the actual thing: a z-score of price against its own intraday mean,
crossed with the holding horizon, entered and exited exactly as the live path
would. The mirror side (z > 0, i.e. stretched UP) is measured too — as a long,
which is what the labels support. A large negative number there is the direct
argument for B1's short side.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Lookback windows for the intraday mean, bracketing the 17-min measured half-life.
Z_WINDOWS = (10, 17, 30)
# Entry thresholds. Negative = stretched below the mean (buy the dip);
# positive = stretched above (the short-on-strength population).
Z_THRESHOLDS = (-3.0, -2.5, -2.0, -1.5, 2.0, 2.5, 3.0)
# Holding horizons, bracketing the half-life.
HORIZONS = (5, 10, 17, 30, 60)

SQUAREOFF_MIN = 14 * 60 + 45
MIN_BARS = 60
MIN_TRADES = 300
MIN_DAYS = 8


def _t_stat(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def stage_mr_entry() -> None:
    from app.research.level_edge_study import BARS_PQ, OUT_DIR, _log
    from app.utils.trade_costs import round_trip_cost_pct

    cost = round_trip_cost_pct()
    bars = pd.read_parquet(BARS_PQ).sort_values(["symbol", "day", "bar_idx"])
    _log(f"mean-reversion entry sweep, cost hurdle {cost:.4f}%")

    rows = []
    for (sym, day), b in bars.groupby(["symbol", "day"], sort=False):
        b = b.reset_index(drop=True)
        close = b["close"].to_numpy(dtype=float)
        openp = b["open"].to_numpy(dtype=float)
        n = len(close)
        if n < MIN_BARS or (close <= 0).any():
            continue
        tmin = pd.to_datetime(b["time"], format="%H:%M")
        mod = (tmin.dt.hour * 60 + tmin.dt.minute).to_numpy()

        s = pd.Series(close)
        for w in Z_WINDOWS:
            # Rolling mean/std use only bars up to and including i — the live
            # information set. shift() is unnecessary because bar i's own close
            # is known when the decision is made on that closed candle.
            mu = s.rolling(w, min_periods=w).mean().to_numpy()
            sd = s.rolling(w, min_periods=w).std(ddof=0).to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                z = (close - mu) / sd
            z[~np.isfinite(z)] = np.nan

            for thr in Z_THRESHOLDS:
                hit = (z <= thr) if thr < 0 else (z >= thr)
                hit &= np.isfinite(z)
                idx = np.flatnonzero(hit)
                idx = idx[idx + 1 < n]
                if idx.size == 0:
                    continue
                for h in HORIZONS:
                    e, x = idx + 1, idx + 1 + h
                    ok = (x < n) & (mod[np.clip(x, 0, n - 1)] <= SQUAREOFF_MIN)
                    if not ok.any():
                        continue
                    ee, xx = e[ok], x[ok]
                    net = (close[xx] - openp[ee]) / openp[ee] * 100.0 - cost
                    rows.append(pd.DataFrame({
                        "day": day, "window": w, "z": thr, "horizon": h, "net": net,
                    }))

    df = pd.concat(rows, ignore_index=True)
    _log(f"  {len(df):,} simulated trades across the grid")

    # Day-equal weighting throughout: mean within a day, then across days.
    per_day = df.groupby(["window", "z", "horizon", "day"]).net.agg(["mean", "size"]).reset_index()
    counts = df.groupby(["window", "z", "horizon"]).size().rename("n")

    out = []
    for (w, z, h), g in per_day.groupby(["window", "z", "horizon"]):
        n = int(counts.loc[(w, z, h)])
        if n < MIN_TRADES or len(g) < MIN_DAYS:
            continue
        vals = g["mean"].to_numpy()
        out.append({"window": w, "z": z, "horizon": h, "n": n, "days": len(g),
                    "net": vals.mean(), "t": _t_stat(vals),
                    "pos_days": 100 * (vals > 0).mean()})
    res = pd.DataFrame(out).sort_values("net", ascending=False).reset_index(drop=True)

    lines: list[str] = []
    add = lines.append
    add("# B2b — Mean-reversion entry: does a real deviation pay?\n")
    add(f"- **Cost hurdle:** {cost:.4f}% round trip, subtracted from every number")
    add(f"- **Grid:** z-window {Z_WINDOWS} x z-threshold {Z_THRESHOLDS} x horizon {HORIZONS}")
    add(f"- **Filter:** >= {MIN_TRADES} trades on >= {MIN_DAYS} days")
    add("- All entries simulated as **longs**, entering at the next bar's open. "
        "Rows with a positive z are price stretched *upward* — a large negative "
        "result there is the argument for shorting that population (B1).\n")

    dips = res[res.z < 0]
    add("## Buying the dip (z below the mean)\n")
    if dips.empty:
        add("_No cell met the sample-size filter._\n")
    else:
        best = dips.iloc[0]
        add(f"**Best cell: z<={best.z}, window {int(best.window)} min, hold "
            f"{int(best.horizon)} min → {best.net:+.4f}%/trade** "
            f"(n={best.n:,}, {int(best.days)} days, t={best.t:.2f}, "
            f"positive on {best.pos_days:.0f}% of days)\n")
        add("| z | window | hold | n | days | net % | t | days+ |")
        add("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in dips.head(20).iterrows():
            add(f"| {r.z} | {int(r.window)} | {int(r.horizon)} | {r.n:,} | {int(r.days)} "
                f"| {r.net:+.4f} | {r.t:.2f} | {r.pos_days:.0f}% |")
        add("")

    tops = res[res.z > 0]
    add("## Buying strength (z above the mean) — the short-side case\n")
    if tops.empty:
        add("_No cell met the sample-size filter._\n")
    else:
        worst = tops.iloc[-1]
        add(f"**Worst cell: z>={worst.z}, window {int(worst.window)} min, hold "
            f"{int(worst.horizon)} min → {worst.net:+.4f}%/trade** "
            f"(n={worst.n:,}, t={worst.t:.2f}). Mirrored as a short and paying "
            f"costs again, the gross case is roughly {-(worst.net + cost) - cost:+.4f}%.\n")
        add("| z | window | hold | n | days | net % | t | days+ |")
        add("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in tops.iterrows():
            add(f"| {r.z} | {int(r.window)} | {int(r.horizon)} | {r.n:,} | {int(r.days)} "
                f"| {r.net:+.4f} | {r.t:.2f} | {r.pos_days:.0f}% |")
        add("")

    prof = res[res.net > 0]
    add("## Verdict\n")
    if prof.empty:
        add("**No cell in the grid is profitable after costs.** Mean reversion is "
            "present in the price series (Hurst 0.358) but is not harvestable on "
            "the long side at this cost level.\n")
    else:
        sig = prof[prof.t > 2]
        add(f"**{len(prof)} of {len(res)} cells are positive after costs**, "
            f"{len(sig)} of them at t>2.\n")
        for _, r in sig.head(10).iterrows():
            add(f"- z<={r.z}, window {int(r.window)}, hold {int(r.horizon)} min: "
                f"**{r.net:+.4f}%** (n={r.n:,}, t={r.t:.2f}, "
                f"positive on {r.pos_days:.0f}% of days)")
        add("")

    with open(f"{OUT_DIR}/mr_entry.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    res.to_parquet(f"{OUT_DIR}/mr_entry.parquet", index=False)
    _log(f"wrote {OUT_DIR}/mr_entry.md")
    print("\n".join(lines))


if __name__ == "__main__":
    stage_mr_entry()
