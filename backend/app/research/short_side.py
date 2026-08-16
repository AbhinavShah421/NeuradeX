"""B1 — would the short side have paid?

Everything measured so far is long-only, and the strongest result in the study
is that longs on strength LOSE: -0.267%/trade against -0.057% for longs on
weakness, and -0.292% for longs entered at z>=2 above the intraday mean. Chan
and the Jalan deck both prescribe the other side of exactly that trade
("Buy on dips, Short on strength"), and B2 found the universe is strongly
mean-reverting (Hurst 0.358, half-life 17 min).

Flipping the sign on a long result is NOT a measurement: the live exit engine is
asymmetric (ATR stop vs target, an SMA5 profit trail, a momentum fast-cut, an
RSI overbought exit), so a short must be simulated under mirrored rules to know
what it would really have returned.

`_simulate_short` mirrors `counterfactual._simulate_policy` rule for rule. The
live labeller is deliberately not modified — this reads the same bars and writes
its own column, so nothing in the trading path changes.

Correctness: `_reflection_test` simulates a short on a series and a long on that
series reflected about the entry price. Under price-symmetric rules the two must
agree, which catches a mirrored inequality pointing the wrong way.

Run:
    python -m app.research.short_side
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

SQUAREOFF_MIN = 14 * 60 + 45


def _simulate_short(bars: list[dict], inds: list[dict], entry_idx: int,
                    policy: dict) -> Optional[float]:
    """Simulate a SHORT opened at bars[entry_idx].open under `policy`.

    Mirror of `_simulate_policy`, term for term:
      entry fills on the SELL side, exit on the BUY side
      gain    = (entry - price)/entry      (profit when price falls)
      lwm     = running LOW-water mark     (was the high-water mark)
      sma5 trail fires when price rises back ABOVE sma5, confirmed by mom5 > 0
      fast_cut fires on strength (sma5 > sma20 and mom5 > +0.15, or mom5 > +0.30)
      rsi_exit fires OVERSOLD (rsi < 25) with mom5 > 0
      cap_trend_extend rides an intact DOWNtrend (price <= sma5 <= sma20)
    """
    from app.utils.trade_costs import buy_fill, sell_fill, charges

    if entry_idx >= len(bars):
        return None
    entry = sell_fill(bars[entry_idx]["open"])      # opening a short = selling
    if entry <= 0:
        return None
    qty = max(1, int(50_000 / entry))

    hold_cap = int(policy.get("hold_cap", 30))
    grace_min = int(policy.get("grace_min", 0))
    trail = policy.get("trail")
    lwm = entry

    exit_price: Optional[float] = None
    last_i = entry_idx
    for i in range(entry_idx, len(bars)):
        last_i = i
        c = bars[i]
        price = c["close"]
        lwm = min(lwm, price)
        held = i - entry_idx
        try:
            h, m = int(c["time"].split(":")[0]), int(c["time"].split(":")[1])
        except (KeyError, ValueError, IndexError):
            h, m = 0, 0

        ind = inds[i]
        gain = (entry - price) / entry * 100          # mirrored
        atr_pct = max(0.5, min(2.5, (ind.get("atr", 0.0) / price * 100) if price else 0.8))
        stop = -max(policy["stop_floor"], policy["stop_atr_mult"] * atr_pct)
        take = max(policy["take_floor"], policy["take_atr_mult"] * atr_pct)
        sma5, sma20, mom5 = ind.get("sma5", 0.0), ind.get("sma20", 0.0), ind.get("mom5", 0.0)

        capped = held >= hold_cap
        if (capped and policy.get("cap_trend_extend")
                and gain > 0 and price <= sma5 <= sma20):   # intact DOWNtrend
            capped = False
        if capped or (h * 60 + m) >= SQUAREOFF_MIN:
            exit_price = buy_fill(price)
            break

        if held < grace_min:
            if gain <= 2 * stop:
                exit_price = buy_fill(price)
                break
            continue
        if gain <= stop or gain >= take:
            exit_price = buy_fill(price)
            break
        if trail == "sma5":
            if (gain >= policy["lock_gain"] and price > sma5
                    and (mom5 > 0 or not policy.get("lock_confirm_mom"))):
                exit_price = buy_fill(price)
                break
        elif trail == "hwm_atr":
            if gain >= policy["lock_gain"] and price > lwm * (1 + atr_pct / 100):
                exit_price = buy_fill(price)
                break
        if policy.get("fast_cut") and gain < 0.5:
            if (sma5 > sma20 and mom5 > 0.15) or mom5 > 0.30:
                exit_price = buy_fill(price)
                break
        if policy.get("rsi_exit") and ind.get("rsi", 50.0) < 25 and mom5 > 0:
            exit_price = buy_fill(price)
            break
    if exit_price is None:
        exit_price = buy_fill(bars[last_i]["close"])

    # charges(buy_turnover_price, sell_turnover_price, qty) puts STT on the
    # second argument. For a short the SELL is the entry, so the arguments swap.
    fees = charges(exit_price, entry, qty)
    pnl = qty * (entry - exit_price) - fees
    return round(pnl / (qty * entry) * 100, 3)


def _reflection_test() -> None:
    """A short on series S must equal a long on S reflected about the entry price,
    under price-symmetric rules (RSI and fast-cut disabled — RSI does not reflect
    linearly). Guards against a mirrored comparison pointing the wrong way."""
    from app.agents.counterfactual import _simulate_policy, LIVE_POLICY

    rng = np.random.default_rng(7)
    pol = {**LIVE_POLICY, "rsi_exit": False, "fast_cut": False}
    worst = 0.0
    for trial in range(40):
        n = 200
        walk = 100 + np.cumsum(rng.normal(0, 0.25, n))
        bars, refl = [], []
        e = walk[1]
        for i, p in enumerate(walk):
            hi, lo = p + 0.1, p - 0.1
            t = f"{9 + (15 + i) // 60:02d}:{(15 + i) % 60:02d}"
            bars.append({"time": t, "open": p, "high": hi, "low": lo,
                         "close": p, "volume": 1000})
            # Reflect about the entry price; high and low swap under reflection.
            refl.append({"time": t, "open": 2 * e - p, "high": 2 * e - lo,
                         "low": 2 * e - hi, "close": 2 * e - p, "volume": 1000})

        from app.agents.counterfactual import _day_indicators
        s = _simulate_short(bars, _day_indicators(bars), 1, pol)
        l = _simulate_policy(refl, _day_indicators(refl), 1, pol)
        if s is None or l is None:
            continue
        worst = max(worst, abs(s - l))
        assert abs(s - l) < 0.02, (
            f"reflection mismatch on trial {trial}: short={s:.4f} vs long={l:.4f}")
    print(f"[level_edge] reflection test passed on 40 random walks "
          f"(max |short - reflected long| = {worst:.4f} pts)", flush=True)


def stage_short_side() -> None:
    from app.agents.counterfactual import LIVE_POLICY, _day_indicators, _bar_index_for_time
    from app.research.level_edge_study import BARS_PQ, FEATURES_PQ, OUT_DIR, _log
    from app.utils.trade_costs import round_trip_cost_pct

    _reflection_test()

    cost = round_trip_cost_pct()
    bars_df = pd.read_parquet(BARS_PQ).sort_values(["symbol", "day", "bar_idx"])
    feat = pd.read_parquet(FEATURES_PQ)
    _log(f"short side: {len(feat):,} decisions, cost hurdle {cost:.4f}%")

    # Several sessions can decide on the same symbol-day-minute, so the decision
    # frame holds duplicate keys. The short label depends only on the bars and the
    # entry index, so simulate each distinct bar ONCE — otherwise the merge below
    # goes many-to-many and silently double-weights those rows.
    want: dict[tuple[str, str], set[str]] = {}
    for sym, day, t in zip(feat.symbol, feat.day, feat.candle_time):
        want.setdefault((sym, day), set()).add(t)

    out = []
    done = 0
    for (sym, day), b in bars_df.groupby(["symbol", "day"], sort=False):
        times = want.get((sym, day))
        if not times:
            continue
        bars = b.to_dict("records")
        inds = _day_indicators(bars)
        for t in sorted(times):
            idx = _bar_index_for_time(bars, t)
            if idx is None or idx + 1 >= len(bars):
                continue
            out.append({"symbol": sym, "day": day, "candle_time": t,
                        "cf_short_pct": _simulate_short(bars, inds, idx + 1, LIVE_POLICY)})
        done += 1
        if done % 100 == 0:
            _log(f"  {done} symbol-days simulated")

    sh = pd.DataFrame(out).dropna(subset=["cf_short_pct"])
    assert not sh.duplicated(["symbol", "day", "candle_time"]).any(), \
        "short labels must be unique per symbol-day-minute or the merge inflates"
    _log(f"  {len(sh):,} distinct bars simulated short")

    before = len(feat)
    m = feat.merge(sh, on=["symbol", "day", "candle_time"], how="inner")
    assert len(m) <= before, f"merge inflated {before:,} -> {len(m):,}"
    _log(f"  {len(m):,} decisions with both a long and a short label "
         f"({100 * len(m) / before:.1f}% of decisions)")

    def day_stats(df: pd.DataFrame, col: str) -> tuple[float, float, float]:
        per_day = df.groupby("day")[col].mean()
        v = per_day.to_numpy()
        v = v[np.isfinite(v)]
        if len(v) < 3 or v.std(ddof=1) == 0:
            return float(v.mean()) if len(v) else float("nan"), float("nan"), float("nan")
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
        return float(v.mean()), float(t), 100.0 * float((v > 0).mean())

    lines: list[str] = []
    add = lines.append
    add("# B1 — Would the short side have paid?\n")
    add(f"- **Sample:** {len(m):,} decisions with both labels, "
        f"{m.symbol.nunique()} symbols, {m.day.nunique()} days")
    add(f"- **Cost hurdle:** {cost:.4f}% round trip, already inside both labels")
    add("- Shorts simulated under `LIVE_POLICY` with every exit rule mirrored; "
        "verified by a reflection test against the live long engine.\n")

    lm, lt, lp = day_stats(m, "cf_pnl_pct")
    sm, st, sp = day_stats(m, "cf_short_pct")
    add("## Overall\n")
    add("| side | net %/trade | t | days positive | win % |")
    add("|---|---:|---:|---:|---:|")
    add(f"| long | {lm:+.4f} | {lt:.2f} | {lp:.0f}% | {100 * (m.cf_pnl_pct > 0).mean():.1f} |")
    add(f"| short | {sm:+.4f} | {st:.2f} | {sp:.0f}% | {100 * (m.cf_short_pct > 0).mean():.1f} |")
    add("")

    # The populations the study flagged as strength — where longs did worst and
    # the short case is therefore strongest a priori.
    c = m["close"]
    pops = {
        "all decisions": pd.Series(True, index=m.index),
        "near day high": m["dist_day_high_pct"] > -0.10,
        "above VWAP": m["vwap_dist_pct"] > 0,
        "prior-day-high break": c > m["pdh"],
        "R1 break": c > m["r1"],
        "gap up and go": (m["day_open"] > m["pdc"] * 1.005) & (m["from_open_pct"] > 0),
        "A-grade promote rule": ((m["from_open_pct"] >= 0.4)
                                 & (m["dist_day_high_pct"] >= -0.10)
                                 & (m["mom5_pct"] >= 0.15)),
        "weakness (below S1)": c < m["s1"],
    }
    add("## By population — where longs lost worst, did shorts win?\n")
    add("| population | n | long % | short % | short t | short days+ |")
    add("|---|---:|---:|---:|---:|---:|")
    rows = []
    for name, mask in pops.items():
        sub = m[mask.fillna(False)]
        if len(sub) < 300 or sub.day.nunique() < 8:
            continue
        a, _, _ = day_stats(sub, "cf_pnl_pct")
        bmean, bt, bpos = day_stats(sub, "cf_short_pct")
        rows.append((name, len(sub), a, bmean, bt, bpos))
        add(f"| {name} | {len(sub):,} | {a:+.4f} | {bmean:+.4f} | {bt:.2f} | {bpos:.0f}% |")
    add("")

    add("## Verdict\n")
    wins = [r for r in rows if r[3] > 0]
    if not wins:
        add("**No population is profitable on the short side either.** The mirror "
            "of a losing long is not a winning short once the real exit rules and "
            "a second round trip of costs are applied.\n")
    else:
        add(f"**{len(wins)} population(s) show a positive short expectancy.**\n")
        for name, n, a, bmean, bt, bpos in sorted(wins, key=lambda r: -r[3]):
            add(f"- **{name}** — short {bmean:+.4f}%/trade (n={n:,}, t={bt:.2f}, "
                f"positive on {bpos:.0f}% of days) vs long {a:+.4f}%")
        add("")
    add("**Caveat regardless of sign:** intraday shorting in Indian cash equity "
        "requires MIS (square-off enforced by the broker) or the F&O segment; "
        "borrow, margin and the stock's F&O eligibility are real constraints this "
        "simulation does not model.\n")

    with open(f"{OUT_DIR}/short_side.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    m[["symbol", "day", "candle_time", "cf_pnl_pct", "cf_short_pct"]].to_parquet(
        f"{OUT_DIR}/short_side.parquet", index=False)
    _log(f"wrote {OUT_DIR}/short_side.md")
    print("\n".join(lines))


if __name__ == "__main__":
    stage_short_side()
