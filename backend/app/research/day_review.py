"""Post-mortem for a single trading day, scored against the Aug 6 study findings.

For every executed trade it reconstructs the entry bar from the 1-second tick
store and answers three questions the study raised:

  1. Was this a STRENGTH entry? The study measured longs on strength at
     -0.267%/trade against -0.057% on weakness, so a day of strength entries is
     a predicted-loss day rather than bad luck.
  2. Would a SHORTER hold have helped? B2's horizon sweep decays monotonically
     (-0.130% at 5 min to -0.259% at 180), and the live cap is 60.
  3. Was ensemble confidence informative about the outcome?

Run:
    python -m app.research.day_review 2026-08-06
"""
from __future__ import annotations

import sys
from datetime import date as _date

import numpy as np
import pandas as pd

HORIZONS = (5, 10, 17, 30, 45, 60)
SQUAREOFF_MIN = 14 * 60 + 45


def _entry_context(bars: list[dict], idx: int) -> dict:
    """Point-in-time picture of the entry bar: where price sat in the day, versus
    VWAP, and how stretched it was from its own 17-minute mean (the measured
    half-life window)."""
    close = np.array([b["close"] for b in bars], dtype=float)
    high = np.array([b["high"] for b in bars], dtype=float)
    low = np.array([b["low"] for b in bars], dtype=float)
    vol = np.array([b["volume"] for b in bars], dtype=float)

    upto = slice(0, idx + 1)
    c = close[idx]
    day_high, day_low = high[upto].max(), low[upto].min()
    day_open = bars[0]["open"]

    tp = (high[upto] + low[upto] + close[upto]) / 3.0
    v = vol[upto]
    vwap = float((tp * v).sum() / v.sum()) if v.sum() > 0 else float(tp.mean())

    w = close[max(0, idx - 16):idx + 1]
    z = float((c - w.mean()) / w.std()) if len(w) >= 5 and w.std() > 0 else float("nan")

    return {
        "from_open_pct": (c - day_open) / day_open * 100,
        "dist_day_high_pct": (c - day_high) / day_high * 100,
        "vwap_dist_pct": (c - vwap) / vwap * 100,
        "z17": z,
    }


def _forward(bars: list[dict], idx: int, h: int, cost: float) -> float:
    """Net % if the trade had been flat-exited h bars after entry."""
    e, x = idx + 1, idx + 1 + h
    if x >= len(bars):
        x = len(bars) - 1
    if e >= len(bars):
        return float("nan")
    return (bars[x]["close"] - bars[e]["open"]) / bars[e]["open"] * 100 - cost


def main(day: str) -> None:
    import asyncio
    from sqlalchemy import text
    from app.data.candle_store import read_bars
    from app.database import postgres
    from app.utils.trade_costs import round_trip_cost_pct

    cost = round_trip_cost_pct()

    async def load():
        if postgres.engine is None:
            await postgres.init_postgres()
        async with postgres.engine.begin() as conn:
            return (await conn.execute(text("""
                SELECT symbol, action,
                       to_char(timestamp_open AT TIME ZONE 'Asia/Kolkata','HH24:MI') AS entry_t,
                       to_char(timestamp_close AT TIME ZONE 'Asia/Kolkata','HH24:MI') AS exit_t,
                       duration_minutes, pnl_pct*100 AS pnl_pct, pnl_abs, outcome,
                       ensemble_confidence
                FROM trade_records
                WHERE date(timestamp_open AT TIME ZONE 'Asia/Kolkata') = CAST(:d AS date)
                ORDER BY timestamp_open
            """), {"d": _date.fromisoformat(day)})).fetchall()

    rows = asyncio.run(load())
    if not rows:
        print(f"no trades on {day}")
        return

    recs = []
    for r in rows:
        sym, action, et, xt, dur, pnl, rs, outcome, conf = r
        bars = read_bars(sym.upper(), day, 60)
        idx = next((i for i, b in enumerate(bars) if b["time"] == et), None)
        rec = {"symbol": sym, "entry": et, "exit": xt, "dur": dur, "pnl_pct": pnl,
               "pnl_rs": rs, "outcome": outcome, "conf": conf}
        if bars and idx is not None:
            rec.update(_entry_context(bars, idx))
            for h in HORIZONS:
                rec[f"h{h}"] = _forward(bars, idx, h, cost)
        recs.append(rec)
    df = pd.DataFrame(recs)

    print(f"\n{'='*78}\nDAY REVIEW — {day}\n{'='*78}")
    n, wins = len(df), (df.outcome == "WIN").sum()
    print(f"{n} trades, {wins} wins ({100*wins/n:.0f}%), "
          f"net Rs{df.pnl_rs.sum():,.0f}, avg {df.pnl_pct.mean():+.3f}%\n")

    print("ENTRY CONTEXT  (was the system buying strength?)")
    print(f"{'symbol':<12}{'entry':>6}{'pnl%':>8}{'fromOpen':>10}{'distHigh':>10}"
          f"{'vwap':>8}{'z17':>7}  verdict")
    for _, r in df.iterrows():
        if pd.isna(r.get("from_open_pct")):
            print(f"{r.symbol:<12}{r.entry:>6}{r.pnl_pct:>8.2f}{'  (no bars)':>35}")
            continue
        strength = (r.dist_day_high_pct > -0.30) or (r.vwap_dist_pct > 0 and r.from_open_pct > 0.3)
        verdict = "STRENGTH" if strength else "weakness/neutral"
        print(f"{r.symbol:<12}{r.entry:>6}{r.pnl_pct:>8.2f}{r.from_open_pct:>10.2f}"
              f"{r.dist_day_high_pct:>10.2f}{r.vwap_dist_pct:>8.2f}{r.z17:>7.2f}  {verdict}")

    have = df.dropna(subset=["from_open_pct"])
    if len(have):
        s = have[(have.dist_day_high_pct > -0.30) |
                 ((have.vwap_dist_pct > 0) & (have.from_open_pct > 0.3))]
        print(f"\n  {len(s)}/{len(have)} entries were into strength; "
              f"they averaged {s.pnl_pct.mean():+.3f}% vs "
              f"{have[~have.index.isin(s.index)].pnl_pct.mean():+.3f}% for the rest")

    print("\nHOLD-PERIOD COUNTERFACTUAL  (net %, flat exit at N minutes)")
    cols = [f"h{h}" for h in HORIZONS if f"h{h}" in df.columns]
    if cols:
        print(f"{'symbol':<12}{'actual':>8}" + "".join(f"{c:>9}" for c in cols))
        for _, r in df.iterrows():
            if pd.isna(r.get(cols[0])):
                continue
            print(f"{r.symbol:<12}{r.pnl_pct:>8.2f}" +
                  "".join(f"{r[c]:>9.2f}" for c in cols))
        means = df[cols].mean()
        print(f"{'MEAN':<12}{df.pnl_pct.mean():>8.2f}" +
              "".join(f"{means[c]:>9.2f}" for c in cols))
        best = means.idxmax()
        print(f"\n  Best flat horizon: {best[1:]} min ({means[best]:+.3f}%) "
              f"vs actual {df.pnl_pct.mean():+.3f}%")

    print("\nCONFIDENCE vs OUTCOME")
    for outcome, g in df.groupby("outcome"):
        print(f"  {outcome:<6} n={len(g)}  mean conf {g.conf.mean():.3f}  "
              f"range {g.conf.min():.3f}-{g.conf.max():.3f}")
    print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-08-06")
