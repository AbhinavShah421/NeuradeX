from app.research.open_movers import parse_rows, rank_board


def _bc(**syms):
    return {k: {"open": o, "prev_close": pc, "close": o, "volume": v} for k, (o, pc, v) in syms.items()}


HIST = [_bc(AAA=(100, 100, 500_000), BBB=(50, 50, 400_000), CCC=(40, 40, 100_000),
            DDD=(20, 20, 900_000), EEE=(60, 60, 600_000), FFF=(80, 80, 700_000))]


def test_ranks_positive_gaps_largest_first():
    today = _bc(AAA=(105, 100, 1), BBB=(51, 50, 1), EEE=(57, 60, 1))
    board = rank_board(today, HIST, 10)
    assert [b["symbol"] for b in board] == ["AAA", "BBB"]       # EEE gapped down
    assert board[0]["gap_pct"] == 5.0


def test_filters_price_liquidity_and_circuit_locks():
    today = _bc(CCC=(44, 40, 1),        # avg volume 100k < 300k
                DDD=(22, 20, 1),        # price 22 < 30
                FFF=(96, 80, 1),        # +20% opens upper-circuit locked
                ZZZ=(110, 100, 1))      # no volume history at all
    assert rank_board(today, HIST, 10) == []


def test_volume_filter_uses_history_not_the_day_itself():
    # today's own (end-of-day) volume is huge, but history says illiquid
    today = _bc(CCC=(44, 40, 50_000_000))
    assert rank_board(today, HIST, 10) == []


def test_parse_keeps_eq_series_only():
    rows = [{"SctySrs": "EQ", "TckrSymb": "abc", "OpnPric": "10", "PrvsClsgPric": "9",
             "HghPric": "12", "LwPric": "9.5", "ClsPric": "11", "TtlTradgVol": "5"},
            {"SctySrs": "BE", "TckrSymb": "XYZ", "OpnPric": "10", "PrvsClsgPric": "9",
             "HghPric": "12", "LwPric": "9.5", "ClsPric": "11", "TtlTradgVol": "5"}]
    assert list(parse_rows(rows)) == ["ABC"]


def test_has_bars_recent_day_trusts_provider_old_day_needs_ticks(monkeypatch):
    from datetime import date, timedelta
    from app.research import open_movers as om
    from app.data import candle_store
    recent = (date.today() - timedelta(days=3)).isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()
    monkeypatch.setattr(candle_store, "day_coverage", lambda s, d: {"ticks": 5000 if s == "CAP" else 0})
    assert om._has_bars("ANY", recent)
    assert om._has_bars("CAP", old)
    assert not om._has_bars("NOCAP", old)
