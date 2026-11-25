from app.agents.short_counterfactual import simulate_short


def _bars(closes, start_min=10 * 60):
    out = []
    for i, c in enumerate(closes):
        m = start_min + i
        out.append({"time": f"{m // 60:02d}:{m % 60:02d}", "open": c, "high": c, "low": c, "close": c})
    return out


def _inds(bars, sma5_off=0.0):
    # sma5 just above price, sma20 above that: an intact downtrend for a short
    return [{"atr": 0.0, "rsi": 50.0, "mom5": -0.1,
             "sma5": b["close"] + 0.01 + sma5_off, "sma20": b["close"] + 0.02 + sma5_off}
            for b in bars]


def test_falling_stock_is_a_short_profit_and_hits_target():
    bars = _bars([100 - 0.2 * i for i in range(40)])      # -0.2 per minute
    r = simulate_short(bars, _inds(bars), 0)
    assert r is not None and r > 2.0                     # target (+2.5%) minus costs


def test_rising_stock_stops_the_short_out_after_grace():
    bars = _bars([100 + 0.1 * i for i in range(60)])
    r = simulate_short(bars, _inds(bars, sma5_off=-1.0), 0)
    assert r is not None and -2.0 < r < -1.5             # -1.5% stop plus costs


def test_flat_tape_loses_only_costs():
    bars = _bars([100.0] * 80)
    r = simulate_short(bars, _inds(bars, sma5_off=-1.0), 0)
    assert r is not None and -0.3 < r < 0                # round-trip cost, no move


def test_squareoff_closes_the_short():
    bars = _bars([100.0, 99.9, 99.8], start_min=14 * 60 + 44)
    r = simulate_short(bars, _inds(bars), 0)
    assert r is not None
