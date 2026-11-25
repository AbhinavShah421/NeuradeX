from datetime import date

from app.research.gap_bar_collector import boards, days_to_collect


def _bc(**s):
    return {k: {"open": o, "prev_close": pc, "close": o, "volume": 1} for k, (o, pc) in s.items()}


HIST = [{k: {"volume": 500_000} for k in ("UP1", "UP2", "DN1", "FLAT", "PENNY", "LOCK")}]


def test_boards_split_ups_and_downs_with_movers_filters():
    today = _bc(UP1=(110, 100), UP2=(103, 100), DN1=(95, 100), FLAT=(100, 100),
                PENNY=(20, 18), LOCK=(120, 100), THIN=(150, 100))
    b = boards(today, HIST)
    assert [r["symbol"] for r in b["up"]] == ["UP1", "UP2"]      # biggest gap first
    assert [r["symbol"] for r in b["down"]] == ["DN1"]
    # FLAT (no gap), PENNY (< Rs30), LOCK (20% circuit), THIN (no volume history) excluded


def test_days_to_collect_skips_today_weekends_and_done_days():
    days = days_to_collect({"2026-11-23"}, date(2026, 11, 25), window=6)
    assert days == ["2026-11-24", "2026-11-20", "2026-11-19"]   # newest first; Nov 23 done, 21-22 weekend
