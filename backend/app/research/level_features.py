"""Point-in-time features for the level-edge study.

Every value on a decision row is computed from bars at or before that decision's
own bar, and from daily candles strictly before that decision's day. The live
path decides on the closed candle at `idx` and enters at `idx + 1`'s open
(counterfactual.py: `_bar_index_for_time` then `_simulate_policy(bars, inds,
idx + 1, ...)`), so using bar `idx` itself is the live information set, not
lookahead. `assert_no_lookahead` re-checks this on the emitted frame.

Two sources, deliberately split:
  own-day intraday  -> tick store 1-min bars (99.6% coverage of decision days)
  prior/multi-day   -> daily candles (the tick store covers only ~41% of prior
                       days, which would silently halve the sample)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OR_WINDOWS = (15, 30)        # opening-range minutes
SWING_LOOKBACK = 20          # bars for the rolling swing high/low
RUNUP_DAYS = (3, 5, 10)      # multi-day run-up windows
NDAY_HIGH = 20               # distance-from-N-day-high window
ATR_BARS = 14
ROUND_STEPS = (10.0, 50.0, 100.0)


# ── per-symbol-day intraday features ─────────────────────────────────────────

def _intraday_frame(b: pd.DataFrame) -> pd.DataFrame:
    """Running, point-in-time intraday features for one symbol-day.

    Every column here uses only bars up to and including its own row: cumulative
    (expanding) aggregates, or rolling windows that look backwards.
    """
    o, h, l, c, v = b["open"], b["high"], b["low"], b["close"], b["volume"]

    f = pd.DataFrame(index=b.index)
    f["bar_idx"] = b["bar_idx"].values
    f["time"] = b["time"].values
    f["close"] = c.values
    f["day_open"] = o.iloc[0]

    # Running day high/low — expanding, so bar i sees only bars 0..i.
    f["day_high"] = h.cummax()
    f["day_low"] = l.cummin()
    f["dist_day_high_pct"] = (c - f["day_high"]) / f["day_high"] * 100.0
    f["dist_day_low_pct"] = (c - f["day_low"]) / f["day_low"] * 100.0

    # True-range ATR (rolling, backwards).
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_BARS, min_periods=3).mean()
    f["atr_pct"] = atr / c * 100.0

    # Cumulative VWAP. The tick store carries real per-minute volume only after
    # enrichment; where the day is unvolumed fall back to an equal-weight mean,
    # matching `_intraday_indicators`' own fallback.
    tp = (h + l + c) / 3.0
    cum_v = v.cumsum()
    if cum_v.iloc[-1] > 0:
        f["vwap"] = (tp * v).cumsum() / cum_v.replace(0, np.nan)
        f["vwap"] = f["vwap"].fillna(tp.expanding().mean())
    else:
        f["vwap"] = tp.expanding().mean()
    f["vwap_dist_pct"] = (c - f["vwap"]) / f["vwap"] * 100.0
    f["vwap_dist_atr"] = f["vwap_dist_pct"] / f["atr_pct"].replace(0, np.nan)

    # Opening range: level fixed after the window, NaN while still forming, so
    # no bar inside the window can "break out" of a range it is still setting.
    minute = np.arange(len(b))
    for w in OR_WINDOWS:
        hi = h.iloc[:w].max() if len(b) >= w else np.nan
        lo = l.iloc[:w].min() if len(b) >= w else np.nan
        formed = minute >= w
        f[f"or{w}_high"] = np.where(formed, hi, np.nan)
        f[f"or{w}_low"] = np.where(formed, lo, np.nan)
        f[f"or{w}_break_up_pct"] = (c - f[f"or{w}_high"]) / f[f"or{w}_high"] * 100.0
        f[f"or{w}_break_dn_pct"] = (c - f[f"or{w}_low"]) / f[f"or{w}_low"] * 100.0

    # Rolling swing levels — shifted so the current bar never sets its own level.
    f["swing_high"] = h.rolling(SWING_LOOKBACK, min_periods=5).max().shift(1)
    f["swing_low"] = l.rolling(SWING_LOOKBACK, min_periods=5).min().shift(1)
    f["swing_high_dist_pct"] = (c - f["swing_high"]) / f["swing_high"] * 100.0
    f["swing_low_dist_pct"] = (c - f["swing_low"]) / f["swing_low"] * 100.0

    # Round-number proximity: distance to the nearest multiple, in % of price.
    for step in ROUND_STEPS:
        nearest = (c / step).round() * step
        f[f"round{int(step)}_dist_pct"] = (c - nearest).abs() / c * 100.0

    # Intraday momentum + a light trend proxy for the pullback tests.
    f["sma5"] = c.rolling(5, min_periods=2).mean()
    f["sma20"] = c.rolling(20, min_periods=5).mean()
    f["mom5_pct"] = c.pct_change(5) * 100.0
    f["from_open_pct"] = (c - f["day_open"]) / f["day_open"] * 100.0

    # Minutes since open, for the time-of-day control.
    tmin = pd.to_datetime(b["time"], format="%H:%M")
    f["minute_of_day"] = tmin.dt.hour * 60 + tmin.dt.minute
    f["minutes_since_open"] = f["minute_of_day"] - f["minute_of_day"].iloc[0]

    return f


# ── per-symbol daily features (prior day and back) ───────────────────────────

def _daily_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Prior-day-and-back context keyed by the day it applies TO.

    Everything is shifted by one row, so the value attached to day D is derived
    from days strictly before D. Nothing here can see D's own candle.
    """
    d = d.sort_values("date").reset_index(drop=True)
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]

    f = pd.DataFrame(index=d.index)
    f["day"] = d["date"].values

    pdh, pdl, pdc, pdo = h.shift(1), l.shift(1), c.shift(1), o.shift(1)
    f["pdh"], f["pdl"], f["pdc"] = pdh, pdl, pdc
    f["pd_range_pct"] = (pdh - pdl) / pdc * 100.0
    f["pd_return_pct"] = (pdc - pdo) / pdo * 100.0

    # Central Pivot Range + classic floor pivots, all from the prior day.
    piv = (pdh + pdl + pdc) / 3.0
    bc = (pdh + pdl) / 2.0
    tc = 2.0 * piv - bc
    f["pivot"], f["cpr_bc"], f["cpr_tc"] = piv, bc, tc
    f["cpr_width_pct"] = (tc - bc).abs() / pdc * 100.0
    rng = pdh - pdl
    f["r1"], f["s1"] = 2 * piv - pdl, 2 * piv - pdh
    f["r2"], f["s2"] = piv + rng, piv - rng
    f["r3"], f["s3"] = pdh + 2 * (piv - pdl), pdl - 2 * (pdh - piv)

    # Multi-day run-up and how far price has corrected off the N-day high.
    for n in RUNUP_DAYS:
        f[f"runup_{n}d_pct"] = (c.shift(1) / c.shift(1 + n) - 1.0) * 100.0
    nd_high = h.shift(1).rolling(NDAY_HIGH, min_periods=5).max()
    f[f"dist_{NDAY_HIGH}d_high_pct"] = (c.shift(1) - nd_high) / nd_high * 100.0

    # Consecutive up-days ending at the prior day — the "rally" in rally-then-correct.
    up = (c > c.shift(1)).astype(int)
    streak = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
    f["up_streak"] = (streak * up).shift(1)

    return f


# ── assembly ─────────────────────────────────────────────────────────────────

def assert_no_lookahead(feat: pd.DataFrame, bars: pd.DataFrame) -> None:
    """Guard the two ways this study could cheat.

    1. An opening-range level must not exist on a bar inside its own window.
    2. The running day high must never exceed the true high of bars 0..idx.
    """
    for w in OR_WINDOWS:
        inside = feat[feat["minutes_since_open"] < w]
        bad = inside[f"or{w}_high"].notna().sum()
        assert bad == 0, f"OR{w} level leaked into {bad} bars inside its own window"

    chk = bars.sort_values(["symbol", "day", "bar_idx"]).copy()
    chk["true_running_high"] = chk.groupby(["symbol", "day"])["high"].cummax()
    m = feat.merge(chk[["symbol", "day", "bar_idx", "true_running_high"]],
                   on=["symbol", "day", "bar_idx"], how="left")
    over = (m["day_high"] > m["true_running_high"] + 1e-9).sum()
    assert over == 0, f"day_high exceeded the true running high on {over} rows"
    print(f"[level_edge] lookahead assertions passed on {len(feat):,} rows", flush=True)


def stage_features() -> None:
    from app.research.level_edge_study import (
        BARS_PQ, DAILY_PQ, DECISIONS_PQ, FEATURES_PQ, _log,
    )

    dec = pd.read_parquet(DECISIONS_PQ)
    bars = pd.read_parquet(BARS_PQ)
    daily = pd.read_parquet(DAILY_PQ)
    _log(f"features: {len(dec):,} decisions, {len(bars):,} bars, {len(daily):,} daily candles")

    _log("computing intraday features per symbol-day…")
    intra = []
    for (sym, day), b in bars.sort_values(["symbol", "day", "bar_idx"]).groupby(["symbol", "day"], sort=False):
        f = _intraday_frame(b.reset_index(drop=True))
        f["symbol"], f["day"] = sym, day
        intra.append(f)
    intra = pd.concat(intra, ignore_index=True)
    _log(f"  {len(intra):,} bar-level feature rows")

    _log("computing daily context per symbol…")
    dailies = []
    for sym, d in daily.groupby("symbol", sort=False):
        f = _daily_frame(d)
        f["symbol"] = sym
        dailies.append(f)
    dailies = pd.concat(dailies, ignore_index=True)
    _log(f"  {len(dailies):,} symbol-day context rows")

    # Decisions carry the bar's clock label; the tick-store bar carries the same.
    feat = dec.merge(intra, left_on=["symbol", "day", "candle_time"],
                     right_on=["symbol", "day", "time"], how="inner", suffixes=("", "_bar"))
    _log(f"  {len(feat):,} decisions matched to a bar "
         f"({100 * len(feat) / len(dec):.1f}% of labelled rows)")

    feat = feat.merge(dailies, on=["symbol", "day"], how="left")
    _log(f"  prior-day context present on {feat['pdc'].notna().sum():,} rows "
         f"({100 * feat['pdc'].notna().mean():.1f}%)")

    assert_no_lookahead(feat, bars)

    feat.to_parquet(FEATURES_PQ, index=False)
    _log(f"wrote {FEATURES_PQ}")
