"""Tests for prior-day / multi-day levels.

Two jobs. The arithmetic must be right, and the display-only boundary must hold:
the moment `levels` is read inside the entry path this stops being a chart
feature and becomes a trading behaviour that needs a gate verdict and a registry
entry. The boundary tests make that impossible to cross by accident.
"""
from __future__ import annotations

import inspect
import re

from app.utils.price_levels import build_levels, multi_day_levels, prior_day_levels


def _day(date, o, h, l, c):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


# ── pivot arithmetic, hand-computed ─────────────────────────────────────────

def test_pivot_and_floor_levels_match_the_textbook_formulas():
    # H=110 L=90 C=100 -> pivot 100, range 20
    lv = prior_day_levels(110, 90, 100)
    assert lv["pivot"] == 100.0
    assert lv["r1"] == 110.0        # 2*100 - 90
    assert lv["s1"] == 90.0         # 2*100 - 110
    assert lv["r2"] == 120.0        # 100 + 20
    assert lv["s2"] == 80.0         # 100 - 20
    assert lv["r3"] == 130.0        # 110 + 2*(100-90)
    assert lv["s3"] == 70.0         # 90 - 2*(110-100)


def test_cpr_is_ordered_and_width_is_relative_to_close():
    lv = prior_day_levels(110, 90, 100)
    assert lv["cprBot"] <= lv["cprTop"]
    # bc = 100, tc = 2*100 - 100 = 100 -> a zero-width CPR on a symmetric day
    assert lv["cprWidthPct"] == 0.0


def test_cpr_top_and_bottom_are_ordered_when_the_close_is_off_centre():
    lv = prior_day_levels(110, 90, 108)     # pivot 102.67, bc 100, tc 105.33
    assert lv["cprBot"] < lv["cprTop"]
    assert lv["cprWidthPct"] > 0


def test_matches_the_research_module_formulas():
    """Guards the duplication: same inputs, same numbers as level_features."""
    pdh, pdl, pdc = 176.9, 174.2, 175.5
    lv = prior_day_levels(pdh, pdl, pdc)
    piv = (pdh + pdl + pdc) / 3.0
    tol = 1e-4                      # the module rounds to 4dp
    assert abs(lv["pivot"] - piv) < tol
    assert abs(lv["r1"] - (2 * piv - pdl)) < tol
    assert abs(lv["s1"] - (2 * piv - pdh)) < tol


# ── degenerate inputs ───────────────────────────────────────────────────────

def test_missing_or_absurd_ohlc_returns_empty_not_an_exception():
    assert prior_day_levels(None, 90, 100) == {}
    assert prior_day_levels(110, 90, None) == {}
    assert prior_day_levels(0, 0, 0) == {}
    assert prior_day_levels("abc", 90, 100) == {}
    assert prior_day_levels(90, 110, 100) == {}     # high below low


def test_zero_close_cannot_divide_by_zero():
    assert prior_day_levels(110, 90, 0) == {}


# ── multi-day swings ────────────────────────────────────────────────────────

def test_multi_day_high_low_spans_the_window():
    days = [_day(f"2026-08-{d:02d}", 100, 100 + d, 90 - d, 95) for d in range(1, 16)]
    md = multi_day_levels(days)
    assert md["high5d"] == 115.0        # last 5 days -> d=11..15, max high 115
    assert md["low15d"] == 75.0         # d=15 -> low 75


def test_thin_windows_are_skipped_rather_than_reported():
    md = multi_day_levels([_day("2026-08-01", 100, 105, 95, 100)])
    assert "high5d" not in md
    assert "high15d" not in md


# ── build_levels: the payload ───────────────────────────────────────────────

def _series(n=15):
    return [_day(f"2026-08-{d:02d}", 100, 110, 90, 100) for d in range(1, n + 1)]


def test_split_is_relative_to_spot():
    out = build_levels(_series(), spot=100.0)
    assert all(l["price"] > 100.0 for l in out["resistance"])
    assert all(l["price"] < 100.0 for l in out["support"])


def test_nearest_levels_come_first_on_both_sides():
    out = build_levels(_series(), spot=100.0)
    res = [l["price"] for l in out["resistance"]]
    sup = [l["price"] for l in out["support"]]
    assert res == sorted(res)                     # nearest overhead first
    assert sup == sorted(sup, reverse=True)       # nearest underfoot first


def test_no_more_than_three_lines_a_side():
    out = build_levels(_series(), spot=100.0)
    assert len(out["resistance"]) <= 3
    assert len(out["support"]) <= 3


def test_spot_defaults_to_prior_close():
    assert build_levels(_series())["ref"] == 100.0


def test_distance_percent_is_signed_from_spot():
    out = build_levels(_series(), spot=100.0)
    assert all(l["distPct"] > 0 for l in out["resistance"])
    assert all(l["distPct"] < 0 for l in out["support"])


def test_empty_or_unusable_input_returns_empty_dict():
    assert build_levels([]) == {}
    assert build_levels(None) == {}
    assert build_levels([{"date": "x"}]) == {}


def test_as_of_is_the_last_row_not_today():
    out = build_levels(_series())
    assert out["asOf"] == "2026-08-15"


# ── camelCase safety (api.ts camelCases every response key) ─────────────────

def test_no_leaf_key_contains_an_underscore():
    """The axios interceptor renames snake_case keys, so a key with an
    underscore would arrive at the chart under a different name."""
    out = build_levels(_series(), spot=100.0)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert "_" not in k, f"key {k!r} will be renamed by the interceptor"
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(out)


# ── the display-only boundary ───────────────────────────────────────────────

def test_levels_are_exposed_in_detail_but_not_in_the_slim_summary():
    from app.services import sessions_service as svc

    assert '"prior_levels"' in inspect.getsource(svc._detail)
    assert '"prior_levels"' not in inspect.getsource(svc._summary)


def test_the_entry_path_never_reads_levels():
    """The boundary that keeps this a chart feature.

    If `_step` starts reading session["levels"], this is no longer display-only:
    it changes what the system trades and needs a validation-gate verdict plus
    an app/research/registry.py entry. Fail loudly rather than let that happen
    silently.
    """
    from app.services import sessions_service as svc

    src = inspect.getsource(svc._step)
    hits = re.findall(r'\[["\']levels["\']\]|\.get\(\s*["\']levels["\']', src)
    assert not hits, (
        "_step now reads session['levels'] — this crosses the display-only "
        "boundary. Register the behaviour in app/research/registry.py and "
        "measure it through app/research/validation.py before removing this test."
    )


# ── partial-bar and ordering guards ─────────────────────────────────────────

def test_an_in_progress_today_bar_is_never_used():
    """A live session's daily bar is PARTIAL — its high/low are only
    today-so-far. Using it would make PDH silently wrong for the whole day."""
    days = _series(14) + [_day("2026-08-20", 100, 999, 1, 100)]
    out = build_levels(days, spot=100.0, today="2026-08-20")
    assert out["asOf"] == "2026-08-14"
    assert all(l["price"] != 999.0 for l in out["resistance"])


def test_rows_are_ordered_by_data_not_by_input_order():
    """Providers disagree on date labelling, so the prior session is whatever
    the last row is after sorting — not whatever arrived last."""
    days = _series(15)
    shuffled = [days[7], days[14], days[0]] + days[1:7] + days[8:14]
    assert build_levels(shuffled)["asOf"] == "2026-08-15"


def test_today_filter_is_optional():
    assert build_levels(_series())["asOf"] == "2026-08-15"


# ── slot allocation: traded levels beat derived ones ────────────────────────

def test_prior_day_high_is_not_crowded_out_by_the_pivot_family():
    """The flaw real data exposed: on RELIANCE 2026-08-19 the CPR band and pivot
    sat within 0.08% of each other and took every resistance slot, hiding PDH —
    the one level the market actually traded."""
    days = [_day(f"2026-08-{d:02d}", 1305, 1320, 1303, 1311) for d in range(1, 16)]
    out = build_levels(days, spot=1311.0)
    labels = [l["label"] for l in out["resistance"]]
    assert "PDH" in labels, f"PDH missing from {labels}"


def test_visually_identical_levels_collapse_to_one():
    days = [_day(f"2026-08-{d:02d}", 100, 110, 90, 100) for d in range(1, 16)]
    out = build_levels(days, spot=100.0)
    for side in ("resistance", "support"):
        prices = [l["price"] for l in out[side]]
        for a, b in zip(prices, prices[1:]):
            assert abs(a - b) / b * 100.0 >= 0.15, f"{side} has duplicate lines {a} {b}"


def test_levels_are_still_presented_nearest_first():
    days = [_day(f"2026-08-{d:02d}", 1305, 1320, 1303, 1311) for d in range(1, 16)]
    out = build_levels(days, spot=1311.0)
    for side in ("resistance", "support"):
        dists = [abs(l["distPct"]) for l in out[side]]
        assert dists == sorted(dists)
