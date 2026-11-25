from app.services.market_context import context_features


def _bars(closes, start_min=9 * 60 + 15):
    out = []
    for i, c in enumerate(closes):
        m = start_min + i
        out.append({"time": f"{m // 60:02d}:{m % 60:02d}", "open": c, "high": c + 0.5,
                    "low": c - 0.5, "close": c, "volume": 100})
    return out


LEVELS = {"resistance": [{"label": "PDH", "price": 110.0}],
          "support": [{"label": "PDC", "price": 98.0}, {"label": "PDL", "price": 95.0}]}


def test_from_open_gap_and_prior_levels():
    w = _bars([100, 101, 102])
    f = context_features(w, LEVELS, [])
    assert f["from_open_pct"] == 2.0
    assert f["gap_pct"] == round((100 / 98 - 1) * 100, 3)
    assert f["pdh_dist_pct"] == round((102 / 110 - 1) * 100, 3)
    assert f["pdl_dist_pct"] == round((102 / 95 - 1) * 100, 3)
    assert f["or_pos_pct"] is None            # opening range not formed yet
    assert f["nifty_from_open_pct"] is None and f["rs_nifty_pct"] is None


def test_nifty_never_uses_bars_after_the_decision():
    w = _bars([100, 101])                     # decision at 09:16
    nifty = _bars([200, 202, 250])            # 09:17 would be lookahead
    f = context_features(w, None, nifty)
    assert f["nifty_from_open_pct"] == 1.0    # 200 -> 202, not 250
    assert f["rs_nifty_pct"] == 0.0           # stock +1.0 vs Nifty +1.0


def test_opening_range_position():
    w = _bars([100] * 15 + [103])             # breaks above the 15-bar range
    f = context_features(w, None, None)
    assert f["or_pos_pct"] == round((103 / 100.5 - 1) * 100, 3)
    w = _bars([100] * 15 + [100.2])
    assert context_features(w, None, None)["or_pos_pct"] == 0.0


def test_missing_levels_are_none_not_errors():
    f = context_features(_bars([100]), {}, None)
    assert f["gap_pct"] is None and f["pdh_dist_pct"] is None
    assert context_features([], None, None) == {}
