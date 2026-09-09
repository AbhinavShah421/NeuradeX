"""Scanner workers: sector heat, mover attribution, and promotion review.

Each worker is a pure function over the sweep's per-symbol records, so these
tests are plain dicts in and assertions out — no Redis, no network, no sweep.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workers.movers import attribute_move, rank_movers
from app.workers.promotion import (REQUIRED_PARAMS, Promotion, build_promotion,
                                   review_all, review_promotion)
from app.workers.sectors import rank_sectors, sector_breadth, sector_tailwind


def row(sym, pct, *, gap=0.0, relvol=1.0, rsi=55.0, grade="B", intraday=None,
        uptrend=False):
    return {"symbol": sym, "change_pct": pct, "gap_pct": gap, "rel_volume": relvol,
            "rsi": rsi, "grade": grade, "action": "BUY", "signal_score": 70,
            "uptrend": uptrend, "above_sma20": uptrend,
            "intraday_pct": pct - gap if intraday is None else intraday,
            "atr_pct": 1.8, "price": 100.0}


SECTORS = {"HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services",
           "SBIN": "Financial Services", "AXISBANK": "Financial Services",
           "KOTAKBANK": "Financial Services",
           "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
           "LONER": "Textiles"}


def sector_of(sym):
    return SECTORS.get(sym, "Other")


# ── Sector worker ───────────────────────────────────────────────────────────

def test_a_broad_sector_move_outranks_one_stock_carrying_an_average():
    """The failure this worker exists to prevent: one name up 18% inside a thin
    sector produces the same MEAN as everyone up 2%, and only one is tradable."""
    rows = [
        # Broad: five banks all up modestly.
        row("HDFCBANK", 1.8), row("ICICIBANK", 2.1), row("SBIN", 1.6),
        row("AXISBANK", 2.4), row("KOTAKBANK", 1.4),
        # Narrow: IT dragged by a single outlier, the rest flat-to-down.
        row("TCS", 18.0), row("INFY", -0.4), row("WIPRO", -0.2), row("HCLTECH", 0.1),
    ]
    out = rank_sectors(rows, sector_of)
    assert out["hot"][0]["sector"] == "Financial Services"

    it = out["by_sector"]["IT"]
    assert it["mean_pct"] > 4.0        # the mean is fooled
    assert it["median_pct"] < 0.2      # the median is not
    assert it["breadth"] == 0.5


def test_a_sector_too_small_to_read_is_reported_but_never_ranked():
    rows = [row("LONER", 9.0)] + [row("TCS", 1.0), row("INFY", 1.1),
                                  row("WIPRO", 0.9), row("HCLTECH", 1.2)]
    out = rank_sectors(rows, sector_of)
    assert out["by_sector"]["Textiles"]["significant"] is False
    assert "Textiles" not in [s["sector"] for s in out["hot"]]
    assert out["hot"][0]["sector"] == "IT"


def test_market_breadth_counts_every_name_not_just_labelled_ones():
    """Sector aggregation drops what the map cannot label; the market reading
    must not inherit that gap."""
    rows = [row("HDFCBANK", 1.0), row("UNKNOWN1", -2.0), row("UNKNOWN2", -3.0)]
    out = rank_sectors(rows, sector_of)
    assert out["market"]["analysed"] == 3
    assert out["market"]["advancing"] == 1
    assert out["market"]["declining"] == 2


def test_sector_tailwind_reports_support_only_when_broad():
    broad = [row(s, 2.0) for s in ("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK")]
    out = rank_sectors(broad, sector_of)
    tw = sector_tailwind("HDFCBANK", sector_of, out)
    assert tw["supporting"] is True and tw["broad"] is True

    split = [row("HDFCBANK", 3.0), row("ICICIBANK", -2.0),
             row("SBIN", 2.0), row("AXISBANK", -2.5)]
    out2 = rank_sectors(split, sector_of)
    assert sector_tailwind("HDFCBANK", sector_of, out2)["supporting"] is False


def test_an_absent_sector_map_degrades_to_one_bucket_rather_than_failing():
    """A cold label cache must not stop the sweep from producing a reading."""
    out = rank_sectors([row("A", 1.0), row("B", 2.0)], lambda _s: "Other")
    assert out["market"]["analysed"] == 2
    assert out["unlabelled"] == 2


# ── Movers attribution ──────────────────────────────────────────────────────

def test_a_gap_led_move_is_named_as_already_finished():
    """+6.2% that gapped +5.8% is not an opportunity, it is a report."""
    a = attribute_move(row("X", 6.2, gap=5.8, relvol=1.1, rsi=68))
    assert a["quality"] == "already happened"
    assert "gapped" in a["headline"]


def test_a_ground_up_move_on_volume_is_in_progress():
    a = attribute_move(row("X", 6.2, gap=0.2, relvol=3.4, rsi=64))
    assert a["quality"] == "in progress"
    assert any("participation" in d for d in a["drivers"])


def test_a_thin_move_is_called_thin():
    a = attribute_move(row("X", 4.0, gap=0.1, relvol=0.5, rsi=60))
    assert a["quality"] == "thin"


def test_an_extended_gainer_is_flagged_as_late():
    a = attribute_move(row("X", 7.0, gap=0.3, relvol=1.4, rsi=79))
    assert a["quality"] == "extended"
    assert any("extended" in d for d in a["drivers"])


def test_a_gap_up_that_faded_is_reported_as_such():
    a = attribute_move(row("X", 1.0, gap=4.0, relvol=1.5, rsi=55, intraday=-3.0))
    assert any("faded" in d for d in a["drivers"])


def test_sector_context_enters_the_attribution():
    rows = [row(s, 2.2) for s in ("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK")]
    ranked = rank_sectors(rows, sector_of)
    tw = sector_tailwind("HDFCBANK", sector_of, ranked)
    a = attribute_move(row("HDFCBANK", 2.2, relvol=2.5), tw)
    assert any("moving with its sector" in d for d in a["drivers"])


def test_the_movers_board_ranks_on_the_move_not_the_setup_score():
    """This board answers 'what moved'. The scorer's board already exists and
    must not quietly replace this one."""
    rows = [row("A", 9.0, grade="D"), row("B", 1.0, grade="A"), row("C", -7.0)]
    out = rank_movers(rows)
    assert out["gainers"][0]["symbol"] == "A"      # despite grade D
    assert out["losers"][0]["symbol"] == "C"


def test_actionable_gainers_exclude_the_finished_ones():
    rows = [row("GAPPED", 8.0, gap=7.6, relvol=1.0),
            row("GRINDING", 5.0, gap=0.1, relvol=3.1, rsi=62)]
    out = rank_movers(rows)
    assert [g["symbol"] for g in out["actionable_gainers"]] == ["GRINDING"]


# ── Promotion review ────────────────────────────────────────────────────────

def test_a_complete_well_grounded_promotion_passes():
    r = row("GRINDING", 5.0, gap=0.1, relvol=3.1, rsi=62)
    r["move_quality"] = "in progress"
    p = build_promotion(r, worker="movers", reason="grinding higher on 3.1x volume with sector support")
    assert review_promotion(p).ok


def test_a_promotion_missing_a_parameter_is_rejected():
    """The core of the request: the reviewer checks that every parameter was
    actually considered, not that the conclusion sounds plausible."""
    r = row("X", 5.0)
    r["move_quality"] = "in progress"
    r["rel_volume"] = None                       # never looked at participation
    p = build_promotion(r, worker="movers", reason="strong move")
    rev = review_promotion(p)
    assert not rev.ok
    assert any("did not consider rel_volume" in x for x in rev.reasons)


def test_every_required_parameter_is_enforced():
    for missing in REQUIRED_PARAMS:
        r = row("X", 5.0)
        r["move_quality"] = "in progress"
        r[missing] = None
        p = build_promotion(r, worker="w", reason="grounds")
        assert not review_promotion(p).ok, f"{missing} was not enforced"


def test_a_finished_gap_move_is_rejected():
    r = row("X", 8.0, gap=7.6, relvol=1.0)
    r["move_quality"] = "already happened"
    p = build_promotion(r, worker="movers", reason="biggest gainer of the day")
    rev = review_promotion(p)
    assert not rev.ok
    assert any("already over" in x for x in rev.reasons)


def test_buying_strength_is_challenged_because_it_is_the_measured_worst_cell():
    """Tests the TREND legs, not RSI. The first live run rejected 0 of 8 because
    every promotion read RSI 54-68 while up 6-13% on the day — this RSI is
    computed on daily candles, so an intraday rip still looks mid-range. Price
    above its own moving averages is what the measured finding means."""
    r = row("X", 9.0, gap=0.4, relvol=3.0, rsi=60, uptrend=True)
    r["move_quality"] = "in progress"
    p = build_promotion(r, worker="movers", reason="top gainer running hard on volume")
    rev = review_promotion(p)
    assert not rev.ok
    assert any("worst cell" in x for x in rev.reasons)


def test_a_gainer_not_yet_in_an_uptrend_is_not_challenged_on_strength():
    """The mirror: a name up on the day but still under its own averages is a
    recovery, not a chase, and the constraint should stay quiet."""
    r = row("X", 9.0, gap=0.4, relvol=3.0, rsi=60, uptrend=False)
    r["move_quality"] = "in progress"
    rev = review_promotion(build_promotion(r, worker="movers", reason="volume-led recovery"))
    assert rev.ok, rev.reasons


def test_extension_is_now_its_own_separate_finding():
    r = row("X", 9.0, gap=0.4, relvol=3.0, rsi=81, uptrend=False)
    r["move_quality"] = "in progress"
    rev = review_promotion(build_promotion(r, worker="movers", reason="volume-led move"))
    assert not rev.ok
    assert any("easy part of this move is behind it" in x for x in rev.reasons)


def test_a_constraint_can_be_argued_past_explicitly():
    """These are not vetoes on principle. A promoter that supplies the case gets
    through; what is refused is a promotion that never considered the point."""
    r = row("X", 9.0, gap=0.4, relvol=3.0, rsi=60, uptrend=True)
    r["move_quality"] = "in progress"
    p = build_promotion(r, worker="movers", reason="breakout from a 3-week base on 3x volume",
                        overrides={"buying_strength"})
    assert review_promotion(p).ok


def test_a_grade_alone_is_not_a_case():
    """A-grade promotions realised 26.1% against a stated 82-95%, so the grade is
    precisely the number that was wrong about them."""
    r = row("X", 2.0, gap=0.1, relvol=1.5, rsi=58, grade="A")
    r["move_quality"] = "in progress"
    p = build_promotion(r, worker="quality", reason="A grade")
    rev = review_promotion(p)
    assert not rev.ok
    assert any("is the whole case" in x for x in rev.reasons)


def test_the_review_records_what_it_considered_even_when_it_passes():
    """A reviewer that only speaks up on rejection cannot be audited."""
    r = row("X", 3.0, gap=0.2, relvol=2.4, rsi=61)
    r["move_quality"] = "in progress"
    rev = review_promotion(build_promotion(r, worker="movers", reason="volume-led advance"))
    assert rev.ok
    # Derived from REQUIRED_PARAMS rather than hardcoded, so adding a parameter
    # does not silently turn this into a test of the old contract.
    assert any(f"parameters present: {len(REQUIRED_PARAMS)}/{len(REQUIRED_PARAMS)}" in c
               for c in rev.considered)
    assert any("move quality" in c for c in rev.considered)


def test_rejections_are_returned_not_dropped():
    """The point of a reviewer is lost if the reason a name missed the watchlist
    is invisible."""
    good = row("GOOD", 3.0, gap=0.2, relvol=2.4, rsi=61)
    good["move_quality"] = "in progress"
    bad = row("BAD", 8.0, gap=7.9, relvol=1.0)
    bad["move_quality"] = "already happened"
    accepted, rejected = review_all([
        build_promotion(good, worker="movers", reason="volume-led advance"),
        build_promotion(bad, worker="movers", reason="top gainer"),
    ])
    assert [p.symbol for p in accepted] == ["GOOD"]
    assert rejected[0]["symbol"] == "BAD"
    assert rejected[0]["review"]["reasons"]
