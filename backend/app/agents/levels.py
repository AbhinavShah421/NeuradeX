"""Intraday support/resistance level map — shared context for all agents.

Builds the FULL day's level structure from every bar seen so far, so the
ensemble's experts deliberate over one shared map instead of each agent
squinting at its own last few bars:

  • pivot swing highs/lows over the whole session (±window bars)
  • clustered into LEVELS: nearby pivots merge (tolerance scales with ATR),
    so a price tested three times is one level with touches=3, not three
    stray numbers — touch count is the level's strength
  • day high/low, morning range and VWAP annotated as levels too

The ensemble computes this once per decision and injects it as
context["levels"]; day_structure grades R/R against it, meanrev confirms
bounces off tested supports, and the session entry gate reads headroom to
the next tested ceiling. Pure function of the candle window — deterministic
in replay/backtest, no I/O.
"""
from __future__ import annotations

_SWING_WINDOW = 4      # pivot lookaround (bars)
_MIN_CLUSTER_TOL_PCT = 0.12   # floor for cluster merge tolerance (%)
_ATR_TOL_MULT = 0.35          # tolerance = max(floor, this × ATR%)


def _atr_pct(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.5
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        pc = candles[i - 1]["close"]
        trs.append(max(candles[i]["high"] - candles[i]["low"],
                       abs(candles[i]["high"] - pc),
                       abs(candles[i]["low"] - pc)))
    price = candles[-1]["close"] or 1.0
    return (sum(trs) / len(trs)) / price * 100 if trs else 0.5


def _pivots(candles: list[dict], window: int) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """(price, bar_index) pivot swing highs and lows over the full window."""
    n = len(candles)
    highs: list[tuple[float, int]] = []
    lows: list[tuple[float, int]] = []
    for i in range(window, n - window):
        h, l = candles[i]["high"], candles[i]["low"]
        nb_h = [candles[j]["high"] for j in range(i - window, i + window + 1) if j != i]
        nb_l = [candles[j]["low"] for j in range(i - window, i + window + 1) if j != i]
        if h >= max(nb_h):
            highs.append((h, i))
        if l <= min(nb_l):
            lows.append((l, i))
    return highs, lows


def _cluster(points: list[tuple[float, int]], tol_pct: float, kind: str) -> list[dict]:
    """Merge pivots within tol into levels with touch counts + recency."""
    if not points:
        return []
    pts = sorted(points)
    levels: list[dict] = []
    cur = [pts[0]]
    for p in pts[1:]:
        anchor = cur[0][0]
        if anchor > 0 and (p[0] - anchor) / anchor * 100 <= tol_pct:
            cur.append(p)
        else:
            levels.append(cur)
            cur = [p]
    levels.append(cur)
    out = []
    for grp in levels:
        prices = [p for p, _ in grp]
        out.append({
            "price": round(sum(prices) / len(prices), 2),
            "touches": len(grp),
            "last_touch_idx": max(i for _, i in grp),
            "kind": kind,
        })
    return out


def compute_levels(candles: list[dict]) -> dict:
    """The shared level map. Supports sorted nearest-below-first, resistances
    nearest-above-first; each {price, touches, dist_pct, kind}."""
    closes = [c.get("close") for c in candles if c.get("close")]
    if len(closes) < 12:
        return {"ok": False, "supports": [], "resistances": []}
    price = closes[-1]
    highs = [c.get("high", c["close"]) for c in candles if c.get("close")]
    lows = [c.get("low", c["close"]) for c in candles if c.get("close")]
    vols = [c.get("volume", 0) or 0 for c in candles if c.get("close")]

    atr = _atr_pct(candles)
    tol = max(_MIN_CLUSTER_TOL_PCT, _ATR_TOL_MULT * atr)

    piv_h, piv_l = _pivots(candles, _SWING_WINDOW)
    lv = _cluster(piv_h, tol, "swing") + _cluster(piv_l, tol, "swing")

    # Structural anchors — day extremes and the morning range.
    m_end = min(45, max(10, len(candles) // 4))
    anchors = [
        (max(highs), "day_high"), (min(lows), "day_low"),
        (max(highs[:m_end]), "morning_high"), (min(lows[:m_end]), "morning_low"),
    ]
    # VWAP as a dynamic level (volume-weighted when volume exists).
    viv = sum(v for v in vols)
    if viv > 0:
        vwap = sum(c * v for c, v in zip(closes, vols)) / viv
    else:
        vwap = sum(closes) / len(closes)
    anchors.append((vwap, "vwap"))

    for ap, kind in anchors:
        # merge into an existing cluster when close enough, else add
        merged = False
        for l in lv:
            if l["price"] > 0 and abs(ap - l["price"]) / l["price"] * 100 <= tol:
                l["touches"] += 1
                if kind in ("day_high", "day_low"):
                    l["kind"] = kind          # structural name wins for display
                merged = True
                break
        if not merged:
            lv.append({"price": round(float(ap), 2), "touches": 1,
                       "last_touch_idx": len(candles) - 1, "kind": kind})

    supports, resistances = [], []
    for l in lv:
        d = (l["price"] - price) / price * 100
        entry = {**l, "dist_pct": round(abs(d), 3)}
        if d < -0.02:
            supports.append(entry)
        elif d > 0.02:
            resistances.append(entry)
        # levels within ±0.02% of price count as being AT the level — expose both ways
        else:
            supports.append({**entry, "at_price": True})
            resistances.append({**entry, "at_price": True})

    supports.sort(key=lambda l: l["dist_pct"])
    resistances.sort(key=lambda l: l["dist_pct"])
    return {"ok": True, "price": round(price, 2), "atr_pct": round(atr, 3),
            "supports": supports[:6], "resistances": resistances[:6]}
