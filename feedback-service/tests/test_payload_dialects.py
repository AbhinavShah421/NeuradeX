"""Two producers write trades here and they do not use the same field names.

The Python side (POST /trades — backtests, paper sessions) speaks the column
names. The Java trade-executor publishes a `TradeOutcome` over trade.outcomes
named for execution time: `fill_price`, `agent_votes`, `executed_at`, `pnl`.

Reading only the column names dropped every executor field that was not
trade_id/symbol/action, silently — `payload.get("entry_price", 0)` just returns
the default. Between 2026-08-18 and 2026-09-08 all 90 executor trades landed as
husks: entry_price 0, confidence 0, no votes, no context, source "LIVE" despite
paper_trade=true. They then rendered on the Orders page as 90 separate blank
one-trade "sessions", because with no market_context there is no session id to
group them by.

These tests pin the normalisation both dialects now go through.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _pick


# ── the executor's dialect ───────────────────────────────────────────────────

def _executor_payload(**over):
    """What TradeOutcome.java actually puts on trade.outcomes."""
    p = {
        "trade_id": "72c3f21c-e081-408e-ab84-a34ee25e0759",
        "symbol": "NIACL",
        "action": "BUY",
        "fill_price": 204.59,
        "fill_qty": 24.45,
        "stop_loss": 201.2,
        "take_profit": 209.6,
        "paper_trade": True,
        "confidence": 0.61,
        "status": "FILLED",
        "agent_votes": {"trend": {"signal": "BUY", "confidence": 0.7}},
        "executed_at": "2026-09-08T09:47:02+00:00",
        "portfolio_value": 100000.0,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    p.update(over)
    return p


def test_entry_price_comes_from_fill_price():
    """The bug in one line: this returned 0 for every executor trade."""
    p = _executor_payload()
    assert _pick(p, "entry_price", "fill_price", default=0) == 204.59


def test_confidence_comes_from_the_executors_field():
    p = _executor_payload()
    assert _pick(p, "ensemble_confidence", "confidence", default=0) == 0.61


def test_votes_come_from_agent_votes():
    p = _executor_payload()
    votes = _pick(p, "agent_signals", "agent_votes", default={})
    assert votes == {"trend": {"signal": "BUY", "confidence": 0.7}}


def test_open_timestamp_comes_from_executed_at():
    p = _executor_payload()
    assert _pick(p, "timestamp_open", "executed_at") == "2026-09-08T09:47:02+00:00"


def test_paper_execution_is_not_recorded_as_live():
    """paper_trade=true with no trade_source was stored under the "LIVE" column
    default, so the Orders page labelled paper fills LIVE."""
    p = _executor_payload()
    paper = bool(_pick(p, "paper_trade", default=False))
    assert p.get("trade_source") is None
    assert ("PAPER" if paper else "LIVE") == "PAPER"


def test_a_real_live_fill_stays_live():
    p = _executor_payload(paper_trade=False)
    paper = bool(_pick(p, "paper_trade", default=False))
    assert ("PAPER" if paper else "LIVE") == "LIVE"


# ── the Python dialect must not regress ─────────────────────────────────────

def _session_payload(**over):
    """What POST /trades sends — the path essentially every trade arrives on."""
    p = {
        "trade_id": "abc",
        "symbol": "IIFL",
        "action": "BUY",
        "entry_price": 412.0,
        "exit_price": 415.5,
        "pnl_abs": 265.18,
        "pnl_pct": 0.0085,
        "ensemble_confidence": 0.74,
        "agent_signals": {"memory": {"signal": "BUY"}},
        "timestamp_open": "2026-09-08T06:34:00+00:00",
        "trade_source": "PAPER",
        "outcome": "WIN",
    }
    p.update(over)
    return p


def test_column_names_still_win_over_the_executor_aliases():
    p = _session_payload(fill_price=1.0, agent_votes={"noise": {}}, pnl=9.0)
    assert _pick(p, "entry_price", "fill_price", default=0) == 412.0
    assert _pick(p, "agent_signals", "agent_votes", default={}) == {"memory": {"signal": "BUY"}}
    assert _pick(p, "pnl_abs", "pnl") == 265.18


def test_an_explicit_trade_source_is_never_overridden():
    p = _session_payload(trade_source="BACKTEST", paper_trade=True)
    paper = bool(_pick(p, "paper_trade", default=False))
    assert (p.get("trade_source") or ("PAPER" if paper else "LIVE")) == "BACKTEST"


# ── _pick itself ────────────────────────────────────────────────────────────

def test_pick_skips_missing_and_null_but_keeps_falsy_values():
    """A real zero must survive — a closed trade at break-even has pnl 0.0, and
    treating that as absent would fall through to the wrong field."""
    assert _pick({"a": None, "b": 0.0}, "a", "b", default=99) == 0.0
    assert _pick({}, "a", "b", default=99) == 99
    assert _pick({"b": 5}, "a", "b") == 5


# ── what the listing hides ──────────────────────────────────────────────────

@pytest.mark.parametrize("entry,outcome,listed", [
    (0.0,    None,   False),   # the husk: nothing readable on it at all
    (204.59, None,   True),    # a genuine open position must still show
    (0.0,    "LOSS", True),    # priced oddly but it resolved — keep it
    (204.59, "WIN",  True),
])
def test_readable_predicate(entry, outcome, listed):
    """Mirrors the SQL in _READABLE: NOT (entry_price = 0 AND outcome IS NULL)."""
    assert (not ((entry or 0) == 0 and outcome is None)) is listed


# ── only a CLOSED trade may move weights ────────────────────────────────────

def _closed(payload: dict) -> bool:
    """Mirrors the guard in _apply_trade_outcome."""
    if payload.get("pnl_pct") is None or payload.get("action") not in ("BUY", "SELL"):
        return False
    return any(payload.get(k) for k in ("exit_price", "timestamp_close", "outcome"))


def test_an_executor_entry_does_not_look_closed():
    """TradeOutcome types pnl_pct as a Java primitive, so an entry serialises it
    as 0.0 rather than omitting it. Read naively that is a break-even close —
    and once agent_votes map through, it would move the weights on a trade whose
    result nobody knows yet."""
    assert _closed(_executor_payload()) is False


def test_an_executor_close_does_look_closed():
    assert _closed(_executor_payload(exit_price=209.6, pnl_pct=0.024)) is True


def test_a_session_close_still_learns():
    """The path essentially every trade arrives on must not regress."""
    assert _closed(_session_payload()) is True


def test_a_session_entry_does_not_learn():
    p = _session_payload()
    for k in ("exit_price", "outcome"):
        p.pop(k)
    assert _closed(p) is False


# ── nothing that executed no trade may be stored ────────────────────────────

def _storable(payload: dict) -> bool:
    """Mirrors the guard in _store_trade_record."""
    entry = _pick(payload, "entry_price", "fill_price", default=0)
    return bool(float(entry or 0) or payload.get("outcome"))


def test_a_husk_is_refused():
    """entry_price 0 and no outcome describes no executed trade. 90 of these
    accumulated from the field mismatch and filled the Orders page."""
    assert _storable({"symbol": "NIACL", "action": "BUY"}) is False


def test_the_old_broken_shape_is_refused():
    """Exactly what the executor's payload used to reduce to once every field
    name missed: action and symbol, nothing else."""
    assert _storable({"trade_id": "x", "symbol": "NIACL", "action": "BUY",
                      "pnl_pct": 0.0}) is False


def test_a_correctly_mapped_executor_entry_is_stored():
    """The same trade, once fill_price maps through — a real open position that
    must NOT be swept up by the guard."""
    assert _storable(_executor_payload()) is True


def test_a_closed_trade_without_an_entry_price_is_still_stored():
    """Odd, but it resolved to a P&L, so it is readable and worth keeping."""
    assert _storable({"symbol": "X", "action": "BUY", "outcome": "LOSS"}) is True


def test_a_normal_session_trade_is_stored():
    assert _storable(_session_payload()) is True
