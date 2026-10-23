"""Movers worker — the day's gainers and losers, and *why*.

Ranking movers is trivial; the value is the attribution. "TATASTEEL +6.2%" tells
you nothing you can act on. "+6.2%, gapped +4.1% at the open and has not added
to it on 0.9x volume" and "+6.2%, opened flat and ground up all session on 3.4x
volume with its sector up 2.1% on 78% breadth" are opposite situations wearing
the same number.

Attribution here is strictly mechanical — every driver is read off figures the
sweep already computed. Nothing infers intent, and nothing calls a model. A
reason that cannot be recomputed from the record is not a reason, it is a story.

The drivers, in the order they are tested:

  gap        the move happened before anyone could trade it
  volume     conviction — the move is carrying real participation
  sector     the name is moving with its group rather than alone
  extension  how far it has run from its own recent range
  reversal   direction disagrees with the gap (faded, or bought back)
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# A gap this large is the dominant fact about the day's move.
_GAP_MATERIAL = 1.5
# Relative volume that marks genuine participation rather than a thin drift.
_VOL_CONVICTION = 2.0
_VOL_THIN = 0.8
# RSI past which a name is extended enough that the move is late by definition.
_RSI_EXTENDED = 72.0
_RSI_WASHED = 28.0


def attribute_move(row: dict, sector_view: Optional[dict] = None) -> dict:
    """Explain one symbol's day move from the sweep's own figures.

    Returns `{"drivers": [...], "headline": str, "quality": str}`. `quality` is
    the honest summary: whether the move is something a trader could still act
    on, or something that already happened without them.
    """
    pct = float(row.get("change_pct") or 0.0)
    gap = row.get("gap_pct")
    relvol = row.get("rel_volume")
    rsi = row.get("rsi")
    intraday = row.get("intraday_pct")
    up = pct >= 0

    drivers: list[str] = []

    # ── Gap: did the move happen before the session? ────────────────────────
    gap_led = False
    if gap is not None and abs(gap) >= _GAP_MATERIAL:
        gap_share = abs(gap) / abs(pct) if pct else 1.0
        if gap_share >= 0.7:
            gap_led = True
            drivers.append(
                f"gapped {gap:+.1f}% at the open — {gap_share:.0%} of the day's "
                f"move was already priced before the bell")
        else:
            drivers.append(f"opened {gap:+.1f}% and added to it during the session")

    # ── Volume: is anyone actually participating? ───────────────────────────
    if relvol is not None:
        if relvol >= _VOL_CONVICTION:
            drivers.append(f"{relvol:.1f}x normal volume — real participation behind it")
        elif relvol <= _VOL_THIN:
            drivers.append(f"only {relvol:.1f}x normal volume — a thin move, easily unwound")

    # ── Sector: moving with the group, or alone? ────────────────────────────
    if sector_view:
        if sector_view.get("supporting") and up:
            drivers.append(
                f"{sector_view['sector']} is up {sector_view['median_pct']:+.1f}% "
                f"on {sector_view['breadth']:.0%} breadth — moving with its sector")
        elif sector_view.get("median_pct") is not None and up and sector_view["median_pct"] < 0:
            drivers.append(
                f"moving against {sector_view['sector']} ({sector_view['median_pct']:+.1f}%) "
                f"— stock-specific, not a sector bid")

    # ── Extension: how much room is left? ───────────────────────────────────
    if rsi is not None:
        if up and rsi >= _RSI_EXTENDED:
            drivers.append(f"RSI {rsi:.0f} — extended, the easy part of this move is behind it")
        elif not up and rsi <= _RSI_WASHED:
            drivers.append(f"RSI {rsi:.0f} — washed out")

    # ── Reversal: intraday disagreeing with the gap ─────────────────────────
    if gap is not None and intraday is not None and abs(gap) >= _GAP_MATERIAL:
        if gap > 0 and intraday < -0.3:
            drivers.append(f"gapped up then faded {intraday:+.1f}% intraday — sellers took the open")
        elif gap < 0 and intraday > 0.3:
            drivers.append(f"gapped down then recovered {intraday:+.1f}% intraday — bought back")

    # ── Quality: can this still be traded, or is it already over? ───────────
    if gap_led and (relvol is None or relvol < _VOL_CONVICTION):
        quality = "already happened"
    elif up and rsi is not None and rsi >= _RSI_EXTENDED:
        quality = "extended"
    elif relvol is not None and relvol >= _VOL_CONVICTION and not gap_led:
        quality = "in progress"
    elif relvol is not None and relvol <= _VOL_THIN:
        quality = "thin"
    else:
        quality = "unclear"

    headline = drivers[0] if drivers else "no distinguishing driver in the scan's figures"
    return {"drivers": drivers, "headline": headline, "quality": quality}


def rank_movers(rows: Iterable[dict], sector_of=None, ranked_sectors: Optional[dict] = None,
                top: int = 20) -> dict[str, Any]:
    """Top gainers and losers across everything analysed, each with its reason.

    Sorted purely on the day's move — this board answers "what moved", and it
    must not quietly become "what the setup scorer liked", which is a different
    board that already exists. The scorer's opinion is carried alongside so the
    two can be read against each other; they routinely disagree, and that
    disagreement is informative rather than a bug.
    """
    rows = [r for r in rows if r.get("change_pct") is not None]

    def view_for(sym: str) -> Optional[dict]:
        if not (sector_of and ranked_sectors):
            return None
        from .sectors import sector_tailwind
        return sector_tailwind(sym, sector_of, ranked_sectors)

    def decorate(r: dict) -> dict:
        why = attribute_move(r, view_for(r.get("symbol", "")))
        return {**r, "why": why["headline"], "drivers": why["drivers"],
                "move_quality": why["quality"]}

    ordered = sorted(rows, key=lambda r: float(r["change_pct"]), reverse=True)
    gainers = [decorate(r) for r in ordered[:top]]
    losers = [decorate(r) for r in ordered[-top:][::-1]]

    # The gainers worth a second look: still moving, with volume behind them,
    # rather than a gap that finished before the open. This is the subset a
    # promotion should ever be drawn from.
    actionable = [g for g in gainers if g["move_quality"] == "in progress"]

    return {
        "gainers": gainers,
        "losers": losers,
        "actionable_gainers": actionable,
        "analysed": len(rows),
    }
