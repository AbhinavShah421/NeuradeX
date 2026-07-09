"""AI regime forecaster — predicts the NEXT session's market regime and tracks
its own accuracy honestly.

Model: smoothed context-Markov chain over the daily regime sequence.
  • The regime label per day is the same rule the live classifier uses
    (SMA20/SMA50 alignment + 5-day momentum), so predictions are measured
    against exactly what the dashboard will show tomorrow.
  • Context = (today's regime, mom5 sign, RSI14 side, SMA-gap widening).
    Transition counts are Jelinek-Mercer smoothed with back-off:
    context → plain Markov row → global label frequency. With ~300 daily
    samples this out-performs iterative learners that overfit, and it trains
    in one O(n) pass (the scanner is numpy-free and calls this every sweep).
  • Evaluation is PREQUENTIAL (predict day t+1 before counting day t's
    transition), so every recorded hit/miss is out-of-sample — the accuracy
    series the UI graphs is never trained-on data. A persistence baseline
    ("tomorrow = today") is reported alongside; if the model can't beat it,
    the UI should say so rather than hide it.

Stateless by design: the full history is recomputed from candles each call
(~300 steps, microseconds), so restarts/redeploys can't corrupt the record.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

_LABELS = ("bullish", "bearish", "neutral")
_LBL_IDX = {l: i for i, l in enumerate(_LABELS)}

# Prediction warm-up: transitions counted but hits/misses not recorded until the
# model has seen this many days (a cold count table guesses near-uniformly).
_MIN_HISTORY = 30
# Back-off support: how many observations a level needs before it dominates.
_JM_STRENGTH = 5.0


# ── Daily label + feature extraction ─────────────────────────────────────────

def _sma(vals: list[float], n: int, i: int) -> float:
    return sum(vals[i - n + 1: i + 1]) / n


def _rsi14(closes: list[float], i: int) -> float:
    gains = losses = 0.0
    for j in range(i - 13, i + 1):
        d = closes[j] - closes[j - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses <= 1e-12:
        return 100.0
    rs = (gains / 14) / (losses / 14)
    return 100.0 - 100.0 / (1.0 + rs)


def _label(closes: list[float], i: int) -> str:
    """Same rule as the live classifier (_market_regime): SMA cross + momentum."""
    sma20 = _sma(closes, 20, i)
    sma50 = _sma(closes, 50, i)
    mom = (closes[i] - closes[i - 5]) / closes[i - 5] * 100
    if sma20 > sma50 and mom > 0:
        return "bullish"
    if sma20 < sma50 and mom < 0:
        return "bearish"
    return "neutral"


def _context(closes: list[float], i: int) -> tuple:
    sma20 = _sma(closes, 20, i)
    sma50 = _sma(closes, 50, i)
    gap = sma20 - sma50
    sma20p = _sma(closes, 20, i - 1)
    sma50p = _sma(closes, 50, i - 1)
    gap_prev = sma20p - sma50p
    mom = (closes[i] - closes[i - 5]) / closes[i - 5] * 100
    return (
        _label(closes, i),
        mom > 0,
        _rsi14(closes, i) > 50,
        gap > gap_prev,
    )


# ── Smoothed prediction from count tables ─────────────────────────────────────

def _probs(ctx: tuple, ctx_counts: dict, markov: dict, global_counts: list[float]) -> list[float]:
    """Jelinek-Mercer back-off: context row → Markov row → global frequency."""
    g_tot = sum(global_counts)
    p_global = [(c + 1.0) / (g_tot + 3.0) for c in global_counts]

    mk_row = markov.get(ctx[0], [0.0, 0.0, 0.0])
    mk_tot = sum(mk_row)
    w_mk = mk_tot / (mk_tot + _JM_STRENGTH)
    p_mk = [
        w_mk * (mk_row[k] / mk_tot if mk_tot > 0 else 0.0) + (1 - w_mk) * p_global[k]
        for k in range(3)
    ]

    cx_row = ctx_counts.get(ctx, [0.0, 0.0, 0.0])
    cx_tot = sum(cx_row)
    w_cx = cx_tot / (cx_tot + _JM_STRENGTH)
    return [
        w_cx * (cx_row[k] / cx_tot if cx_tot > 0 else 0.0) + (1 - w_cx) * p_mk[k]
        for k in range(3)
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def regime_forecast(candles: list[dict]) -> dict:
    """Run the prequential forecast over daily candles ({c: close, t: epoch}).

    Returns the payload attached to regime-detail as `ai`:
      prediction — next-session regime with class probabilities
      accuracy   — out-of-sample record: overall, recent-20, persistence
                   baseline, and a rolling-20 series for the sparkline
    """
    closes = [float(c["c"]) for c in candles]
    times = [int(c.get("t") or 0) for c in candles]
    n = len(closes)
    first = 52  # SMA50 at i-1 plus momentum lookback
    if n < first + _MIN_HISTORY + 2:
        return {"status": "insufficient_history", "days": n}

    ctx_counts: dict[tuple, list[float]] = {}
    markov: dict[str, list[float]] = {}
    global_counts = [0.0, 0.0, 0.0]

    record: list[dict] = []   # one entry per out-of-sample prediction
    seen = 0

    for i in range(first, n - 1):
        ctx = _context(closes, i)
        actual = _label(closes, i + 1)
        if seen >= _MIN_HISTORY:
            p = _probs(ctx, ctx_counts, markov, global_counts)
            k = max(range(3), key=lambda j: p[j])
            d = (datetime.fromtimestamp(times[i + 1], IST).strftime("%Y-%m-%d")
                 if times[i + 1] else str(i + 1))
            record.append({
                "d": d,
                "predicted": _LABELS[k],
                "actual": actual,
                "correct": _LABELS[k] == actual,
                "persist_correct": ctx[0] == actual,
            })
        # update AFTER predicting — keeps every recorded point out-of-sample
        ai = _LBL_IDX[actual]
        ctx_counts.setdefault(ctx, [0.0, 0.0, 0.0])[ai] += 1
        markov.setdefault(ctx[0], [0.0, 0.0, 0.0])[ai] += 1
        global_counts[ai] += 1
        seen += 1

    # Live forecast for the next session from the latest bar's context
    ctx_now = _context(closes, n - 1)
    p_now = _probs(ctx_now, ctx_counts, markov, global_counts)
    k_now = max(range(3), key=lambda j: p_now[j])

    n_rec = len(record)
    hits = sum(1 for r in record if r["correct"])
    persist_hits = sum(1 for r in record if r["persist_correct"])
    recent = record[-20:]
    recent_hits = sum(1 for r in recent if r["correct"])

    # Rolling-20 accuracy series for the sparkline (one point per session)
    series = []
    window: list[bool] = []
    for r in record:
        window.append(r["correct"])
        if len(window) > 20:
            window.pop(0)
        if len(window) >= 10:   # first points on a 10+ window, else too jumpy
            series.append({"d": r["d"], "a": round(sum(window) / len(window), 3)})

    as_of = (datetime.fromtimestamp(times[-1], IST).strftime("%Y-%m-%d")
             if times[-1] else None)
    return {
        "model": "context-markov v1 (prequential)",
        "prediction": {
            "regime": _LABELS[k_now],
            "probs": {_LABELS[j]: round(p_now[j], 3) for j in range(3)},
            "for_session": "next",
            "as_of": as_of,
        },
        "accuracy": {
            "overall": round(hits / n_rec, 3) if n_rec else None,
            "recent20": round(recent_hits / len(recent), 3) if recent else None,
            "persistence": round(persist_hits / n_rec, 3) if n_rec else None,
            "n": n_rec,
            "series": series,
        },
        # last few calls for the modal's mini-table (most recent first)
        "recent": [
            {"d": r["d"], "predicted": r["predicted"], "actual": r["actual"], "correct": r["correct"]}
            for r in record[-8:][::-1]
        ],
    }
