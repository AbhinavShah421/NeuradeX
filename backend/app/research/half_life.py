"""B2 — what is the right intraday holding period?

The 2026-07-30 audit closed exits, the memory bank and ATR filtering, and named
three remaining escape routes: new features, a different horizon, or a cheaper
instrument. The horizon one was never measured — the live hold cap is 60 minutes
and every exit variant ever A/B'd capped at <=60, so nothing has ever tested
outside that range. The Jalan deck prescribes 10 min to 3 hours for Indian
intraday, which straddles it.

Two independent answers to the same question:

  1. THEORY  — Ornstein-Uhlenbeck half-life per symbol-day. Regress the one-bar
     change on the lagged level; b < 0 means mean-reverting and the half-life is
     -ln(2)/b bars. b >= 0 means trending, and no half-life exists.

  2. EMPIRICS — a horizon sweep. Enter at the next bar's open exactly as the live
     path does, exit at a fixed horizon, net of the real round-trip cost, and read
     off where expectancy actually peaks.

If both point at the same number, that is a strong answer. If they disagree,
the sweep wins: it is measured P&L, not a fitted parameter.

Run:
    python -m app.research.half_life
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Horizons in minutes (bars). Spans the live cap (60) and the Jalan band (10-180).
HORIZONS = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180)

SQUAREOFF_MIN = 14 * 60 + 45      # live forced square-off, matches _tech_signal
MIN_BARS_FOR_FIT = 60             # don't fit an OU on a stub of a day
HL_CLIP = 1000                    # cap absurd half-lives before summarising


def _ou_half_life(logp: np.ndarray) -> float:
    """Half-life in bars from an OU fit, or NaN when the series is not mean-reverting.

    dy_t = a + b*y_{t-1} + e ;  b < 0 => mean-reverting, half-life = -ln(2)/b.
    """
    y = logp[:-1]
    dy = np.diff(logp)
    if len(y) < MIN_BARS_FOR_FIT or np.std(y) == 0:
        return np.nan
    b = np.polyfit(y, dy, 1)[0]
    if b >= 0:                    # trending / random walk — no mean reversion
        return np.nan
    return float(-np.log(2.0) / b)


def _hurst(logp: np.ndarray, max_lag: int = 60) -> float:
    """Hurst exponent via the lagged-variance slope. H<0.5 mean-reverting,
    H=0.5 random walk, H>0.5 trending. A model-free cross-check on the OU fit."""
    if len(logp) < max_lag * 2:
        return np.nan
    lags = np.arange(2, max_lag)
    tau = [np.sqrt(np.std(logp[lag:] - logp[:-lag])) for lag in lags]
    tau = np.array(tau)
    ok = tau > 0
    if ok.sum() < 10:
        return np.nan
    return float(np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)[0] * 2.0)


def stage_half_life() -> None:
    from app.research.level_edge_study import BARS_PQ, DECISIONS_PQ, OUT_DIR, _log
    from app.utils.trade_costs import round_trip_cost_pct

    cost = round_trip_cost_pct()
    bars = pd.read_parquet(BARS_PQ).sort_values(["symbol", "day", "bar_idx"])
    dec = pd.read_parquet(DECISIONS_PQ)
    _log(f"half-life: {len(bars):,} bars, cost hurdle {cost:.4f}%")

    # Decision bars, so the sweep can be run on the population the system
    # actually considers as well as on the market unconditionally.
    dec_keys = set(zip(dec.symbol, dec.day, dec.candle_time))

    hl_rows, sweep_rows = [], []
    for (sym, day), b in bars.groupby(["symbol", "day"], sort=False):
        b = b.reset_index(drop=True)
        close = b["close"].to_numpy(dtype=float)
        openp = b["open"].to_numpy(dtype=float)
        if len(close) < MIN_BARS_FOR_FIT or (close <= 0).any():
            continue
        logp = np.log(close)

        hl_rows.append({"symbol": sym, "day": day, "bars": len(close),
                        "half_life": _ou_half_life(logp), "hurst": _hurst(logp)})

        # Minute-of-day per bar, to enforce the live square-off.
        tmin = pd.to_datetime(b["time"], format="%H:%M")
        mod = (tmin.dt.hour * 60 + tmin.dt.minute).to_numpy()
        is_dec = np.array([(sym, day, t) in dec_keys for t in b["time"]])

        n = len(close)
        for h in HORIZONS:
            # Enter at next bar's open (the live entry), exit h bars later at close.
            i = np.arange(n - h - 1)
            if len(i) == 0:
                continue
            entry_i, exit_i = i + 1, i + 1 + h
            valid = mod[exit_i] <= SQUAREOFF_MIN
            if not valid.any():
                continue
            e, x, src = entry_i[valid], exit_i[valid], i[valid]
            gross = (close[x] - openp[e]) / openp[e] * 100.0
            net = gross - cost
            sweep_rows.append(pd.DataFrame({
                "symbol": sym, "day": day, "horizon": h,
                "net": net, "on_decision": is_dec[src],
            }))

    hl = pd.DataFrame(hl_rows)
    sweep = pd.concat(sweep_rows, ignore_index=True)
    _log(f"  {len(hl):,} symbol-days fitted, {len(sweep):,} horizon observations")

    # ── Half-life summary ────────────────────────────────────────────────────
    mr = hl.half_life.notna()
    hl_ok = hl.loc[mr, "half_life"].clip(upper=HL_CLIP)
    lines: list[str] = []
    add = lines.append
    add("# B2 — What is the right intraday holding period?\n")
    add(f"- **Sample:** {hl.symbol.nunique()} symbols, {hl.day.nunique()} days, "
        f"{len(hl):,} symbol-days of 1-minute bars")
    add(f"- **Cost hurdle:** {cost:.4f}% round trip, already subtracted from every "
        "number below\n")

    add("## 1. Ornstein-Uhlenbeck half-life\n")
    add(f"- Mean-reverting (b<0) on **{100 * mr.mean():.1f}%** of symbol-days; "
        f"the rest are trending or random-walk and have no half-life.")
    if len(hl_ok):
        add(f"- Half-life among those: median **{hl_ok.median():.0f} min**, "
            f"25th pct {hl_ok.quantile(.25):.0f}, 75th pct {hl_ok.quantile(.75):.0f}")
    hu = hl.hurst.dropna()
    if len(hu):
        add(f"- **Hurst exponent:** median **{hu.median():.3f}** "
            f"({100 * (hu < 0.5).mean():.1f}% of symbol-days below 0.5). "
            "Below 0.5 is mean-reverting, 0.5 is a random walk, above is trending.")
    add("")

    # ── Horizon sweep ────────────────────────────────────────────────────────
    def curve(df: pd.DataFrame) -> pd.DataFrame:
        # Day-equal weighting: mean within a day, then across days, so the four
        # replay-heavy days cannot decide the shape.
        per_day = df.groupby(["horizon", "day"]).net.mean().reset_index()
        g = per_day.groupby("horizon").net
        out = g.agg(["mean", "std", "size"]).rename(
            columns={"mean": "net", "size": "days"})
        out["t"] = out.net / (out["std"] / np.sqrt(out.days))
        out["pos_days"] = per_day[per_day.net > 0].groupby("horizon").size().reindex(
            out.index).fillna(0) / out.days * 100
        return out.reset_index()

    for label, sub in [("All bars (market-wide)", sweep),
                       ("Decision bars only", sweep[sweep.on_decision])]:
        c = curve(sub)
        if c.empty:
            continue
        add(f"## 2. Horizon sweep — {label}\n")
        add("Enter at the next bar's open, exit after N minutes, net of costs. "
            "Day-equal weighted.\n")
        add("| horizon (min) | net % | t | days | days positive |")
        add("|---:|---:|---:|---:|---:|")
        for _, r in c.iterrows():
            add(f"| {int(r.horizon)} | {r.net:+.4f} | {r.t:.2f} | {int(r.days)} "
                f"| {r.pos_days:.0f}% |")
        best = c.loc[c.net.idxmax()]
        add(f"\n**Peak at {int(best.horizon)} min** ({best.net:+.4f}%, t={best.t:.2f}). "
            f"The live hold cap is 60 min.\n")

    with open(f"{OUT_DIR}/half_life.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    hl.to_parquet(f"{OUT_DIR}/half_life.parquet", index=False)
    _log(f"wrote {OUT_DIR}/half_life.md")
    print("\n".join(lines))


if __name__ == "__main__":
    stage_half_life()
