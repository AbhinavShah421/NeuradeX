"""Promotion — a micro-scanner's nomination, and the review it must survive.

A worker that spots something does not get to put it in front of the trader on
its own. It files a **Promotion**: which worker, which symbol, on what grounds,
with every figure the claim rests on. A **reviewer** then checks the nomination
against the evidence, and only a promotion that passes reaches the watchlist.

**Why the reviewer is not another opinion.** The obvious build is a model that
reads the setup and says yes or no. That was measured here and does not work —
the 8B's verdict tracked how the prompt was framed rather than the precedent it
was given. So this reviewer, like the pre-trade validator, checks facts: are the
parameters the promotion claims to have considered actually present, are they
internally consistent, and does the nomination contradict something already
measured about this system.

**The measured constraints it enforces**, because a promoter chasing the day's
biggest gainer will otherwise walk straight into them:

  * Buying strength is this system's worst cell — 22.6% win against 28.6% for
    everything else. A promotion into an extended move needs the case made, not
    assumed.
  * A-grade promotions realised 26.1% against a stated win probability of
    0.82-0.95. A grade alone is not evidence; that is precisely the number the
    grade was wrong about.
  * A gap-led move already happened. Promoting it offers the trader the part of
    the move that is finished.

None of these are vetoes on principle — every one can be overridden explicitly
by a promoter that supplies the missing evidence. What they refuse is a
promotion that never considered them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Every parameter a promotion must have looked at. A nomination missing any of
# these is not "probably fine" — it is a nomination that did not consider the
# thing, which is exactly what the review exists to catch.
REQUIRED_PARAMS = (
    "change_pct",     # the move itself
    "rel_volume",     # is anyone participating
    "gap_pct",        # did it happen before the open
    "rsi",            # how extended
    "uptrend",        # price over its own moving averages — the measured cell
    "grade",          # what the setup scorer concluded
    "move_quality",   # the movers worker's attribution
)

# Realised win rate of A-grade promotions against a stated 0.82-0.95.
_AGRADE_REALISED = 0.261


@dataclass
class Promotion:
    """One micro-scanner's nomination of a symbol."""

    symbol: str
    worker: str                       # which micro-scanner nominated it
    reason: str                       # plain-language grounds
    params: dict[str, Any]            # every figure the claim rests on
    grade: Optional[str] = None
    sector: Optional[dict] = None     # sector_tailwind() output, if any
    overrides: set = field(default_factory=set)   # constraints explicitly argued past

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "worker": self.worker, "reason": self.reason,
                "grade": self.grade, "params": self.params, "sector": self.sector,
                "overrides": sorted(self.overrides)}


@dataclass
class Review:
    """The reviewer's answer. `ok` only when nothing was raised."""

    ok: bool
    reasons: list[str]
    considered: list[str]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons),
                "considered": list(self.considered)}


def build_promotion(row: dict, worker: str, reason: str, *,
                    sector: Optional[dict] = None,
                    overrides: Optional[set] = None) -> Promotion:
    """Assemble a nomination from a sweep row, carrying every required figure.

    Built from the row rather than hand-filled so a promoter cannot omit a
    parameter by forgetting it — if the sweep did not produce it, the review
    will say so.
    """
    return Promotion(
        symbol=row.get("symbol", ""),
        worker=worker,
        reason=reason,
        grade=row.get("grade"),
        sector=sector,
        overrides=set(overrides or ()),
        params={k: row.get(k) for k in REQUIRED_PARAMS},
    )


def review_promotion(p: Promotion) -> Review:
    """Check a nomination before it reaches the watchlist.

    Every finding is collected rather than returning on the first, because a
    promotion is usually weak in more than one way and the operator should see
    the whole picture in the log.
    """
    reasons: list[str] = []
    considered: list[str] = []

    # ── Completeness: did the promoter actually look at everything? ─────────
    missing = [k for k in REQUIRED_PARAMS if p.params.get(k) is None]
    considered.append(f"parameters present: {len(REQUIRED_PARAMS) - len(missing)}"
                      f"/{len(REQUIRED_PARAMS)}")
    if missing:
        reasons.append(
            f"promotion did not consider {', '.join(missing)} — a nomination that "
            f"skipped a parameter has not been evaluated, it has been guessed")

    if not p.symbol:
        reasons.append("promotion has no symbol")
    if not p.reason:
        reasons.append("promotion states no grounds")

    pct = p.params.get("change_pct")
    relvol = p.params.get("rel_volume")
    rsi = p.params.get("rsi")
    quality = p.params.get("move_quality")

    # ── The move must still be available ────────────────────────────────────
    if quality is not None:
        considered.append(f"move quality: {quality}")
        if quality == "already happened" and "gap_led" not in p.overrides:
            reasons.append(
                "the move is gap-led and finished before the open — promoting it "
                "offers the part that is already over (override: gap_led)")
        if quality == "thin" and "thin_volume" not in p.overrides:
            reasons.append(
                f"thin participation ({relvol}x normal) — a move nobody is trading "
                f"unwinds as easily as it formed (override: thin_volume)")

    # ── The measured worst cell ─────────────────────────────────────────────
    # This tests the TREND legs, not RSI. The first live run rejected nothing
    # because every accepted promotion read RSI 54-68 while up 6-13% on the day
    # — this RSI is computed on daily candles, so a name can rip intraday and
    # still look mid-range. "Buying strength" in the measured finding means
    # price above its own moving averages, which is what `uptrend` carries.
    uptrend = p.params.get("uptrend")
    if uptrend is not None and pct is not None:
        considered.append(f"trend: {'above both SMAs' if uptrend else 'not in uptrend'}")
        if pct > 0 and uptrend and "buying_strength" not in p.overrides:
            reasons.append(
                "promoting a name already above both its moving averages: buying "
                "strength is this system's measured worst cell (22.6% win vs 28.6% "
                "for everything else) (override: buying_strength)")
    if rsi is not None and pct is not None:
        considered.append(f"extension: RSI {rsi}")
        if pct > 0 and rsi >= 72 and "extended" not in p.overrides:
            reasons.append(
                f"RSI {rsi:.0f} — the easy part of this move is behind it "
                f"(override: extended)")

    # ── A grade is not evidence ─────────────────────────────────────────────
    if p.grade:
        considered.append(f"grade: {p.grade}")
        grounds = (p.reason or "").lower()
        only_grade = p.grade in ("A", "A+") and (
            "grade" in grounds and len(grounds.split()) <= 6)
        if only_grade and "grade_only" not in p.overrides:
            reasons.append(
                f"grade {p.grade} is the whole case: A-grade promotions realised "
                f"{_AGRADE_REALISED:.1%} against a stated 82-95% win probability, "
                f"so the grade is the number that was wrong (override: grade_only)")

    # ── Sector context is expected, not required ────────────────────────────
    if p.sector:
        considered.append(
            f"sector {p.sector.get('sector')} {p.sector.get('median_pct')}% "
            f"breadth {p.sector.get('breadth')}")
    else:
        considered.append("sector context: none (symbol unlabelled or sector too small)")

    return Review(ok=not reasons, reasons=reasons, considered=considered)


def review_all(promotions: list[Promotion]) -> tuple[list[Promotion], list[dict]]:
    """Review a batch. Returns (accepted, rejected-with-reasons).

    Rejections are returned rather than dropped: the point of a reviewer is lost
    if the reason a name did not make the watchlist is invisible.
    """
    accepted: list[Promotion] = []
    rejected: list[dict] = []
    for p in promotions:
        r = review_promotion(p)
        if r.ok:
            accepted.append(p)
        else:
            rejected.append({**p.to_dict(), "review": r.to_dict()})
    return accepted, rejected
