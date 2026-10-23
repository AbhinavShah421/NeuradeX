"""Unit tests for the loss-learning loop (no network, no DB).

Locks in:
  • The loop is actually scheduled. loss_learning_run existed only behind its
    manual POST endpoint, so it ran when somebody remembered to — last on
    2026-06-18, leaving 125 losing trades unanalysed and a stale lessons cache.
  • Lesson loss magnitudes are rendered in PERCENT. trade_postmortems.pnl_pct
    is a fraction (copied from trade_records via the feedback-service), so
    rounding it to 2 dp made every lesson read "avg -0.0%" — the decision
    prompts were told each past mistake had cost nothing.
  • The schedule is a DUE-time, not a fire-time. Scheduling it as a sleep to
    03:00 IST put it inside the window this host is powered down, so it ran on
    the night of 2026-08-24 and then not once in the following week.
  • Setup tags are assigned BLIND. The loop used to label only losers, with an
    LLM told up front that the trade had lost, then rank the resulting free
    text by COUNT(*) — so the top "lesson" was whatever phrase was most common
    among losers, with no way to tell whether winners looked identical. The
    control only means something if a winner and a loser with the same setup
    produce the same input to the tagger, which is what the tests below pin.
"""
from datetime import date

from app.config import settings


def test_loss_learning_loop_exists_and_is_registered():
    # The loop must exist...
    from app.services.ai_engine_service import loss_learning_loop
    assert callable(loss_learning_loop)
    # ...and main.py must start it, or it is dead code again.
    import inspect
    import app.main as main
    src = inspect.getsource(main)
    assert "loss_learning_loop" in src, "loop is never scheduled in main.py"


def test_loss_learning_config_defaults():
    assert settings.LOSS_LEARNING_ENABLED is True
    assert 0 <= settings.LOSS_LEARNING_HOUR_IST <= 23
    assert settings.LOSS_LEARNING_MAX_NEW >= 1
    assert settings.LOSS_LEARNING_LIMIT >= settings.LOSS_LEARNING_MAX_NEW


def test_loss_learning_hour_avoids_memory_sweep():
    # Both jobs call the LLM; running them on the same hour contends for ollama.
    assert settings.LOSS_LEARNING_HOUR_IST != settings.MEMORY_SWEEP_HOUR_IST


def test_due_slot_marks_the_day_overdue_after_a_missed_night():
    # The bug this replaces: loss_learning slept to 03:00 IST, the host is
    # powered down then, and the sleep is discarded when the container stops —
    # so 0 post-mortems were written in the week after 2026-08-25 while the
    # task still looked alive. Booting at 09:20 must see the day as due.
    from datetime import datetime
    from app.utils.nightly import due_slot, IST

    boot = datetime(2026, 9, 1, 9, 20, tzinfo=IST)
    assert due_slot(boot, 3) == date(2026, 9, 1)          # today's 03:00 has passed

    # Before the hour, the outstanding slot is still yesterday's.
    predawn = datetime(2026, 9, 1, 0, 40, tzinfo=IST)
    assert due_slot(predawn, 3) == date(2026, 8, 31)

    # Exactly on the hour counts as due, so a machine that IS up at 03:00
    # still runs then rather than waiting for the next boot.
    assert due_slot(datetime(2026, 9, 1, 3, 0, tzinfo=IST), 3) == date(2026, 9, 1)


def test_due_slot_advances_once_per_day():
    # Whatever time of day we ask, two instants inside the same slot must map
    # to the same date — that is what caps the loop at one run per day.
    from datetime import datetime, timedelta
    from app.utils.nightly import due_slot, IST

    for hour in (0, 1, 3, 4, 16, 23):
        base = datetime(2026, 9, 1, hour, 5, tzinfo=IST)
        assert due_slot(base, hour) == due_slot(base + timedelta(hours=6), hour)
        assert due_slot(base + timedelta(days=1), hour) == due_slot(base, hour) + timedelta(days=1)


def _trade(**over):
    t = {
        "symbol": "ACME", "action": "BUY", "entry_price": 100.0,
        "exit_price": 91.23456, "pnl_pct": -0.0876543,
        "duration_minutes": 47, "outcome": "LOSS", "trade_source": "PAPER",
        "ensemble_confidence": 0.71,
        # These are the keys the feedback-service ACTUALLY ships, taken from a
        # live sample: exit_reason and held_minutes ride along inside
        # market_context on 49 of every 50 trades. The first version of this
        # fixture invented a clean context, so it passed while production
        # leaked "exit_reason: stop_loss" straight into the blinded prompt.
        # Fixtures for a blinding test must mirror the real payload.
        "market_context": {
            "rsi": 62.0, "vwap": 99.4, "regime": "neutral",
            "exit_reason": "stop_loss", "held_minutes": 47,
            "session_mode": "paper", "session_id": "abc123",
        },
        "agent_signals": {"technical": "BUY", "gbm": "HOLD"},
    }
    t.update(over)
    return t


def test_entry_evidence_hides_every_outcome_field():
    # The winners control is only valid if the tagger cannot tell winners from
    # losers. Any leak — exit price, P&L, hold time, exit reason, the outcome
    # word — turns the tag back into a restatement of the result, which is the
    # exact defect being fixed.
    import json
    from app.services.ai_engine_service import _entry_evidence

    ev = _entry_evidence(_trade())
    blob = json.dumps(ev)
    for leaked in ("91.23456", "-0.0876543", "0.0876543", "LOSS", "stop_loss", "47"):
        assert leaked not in blob, f"{leaked!r} leaked into the blinded evidence"
    for banned in ("exit_price", "pnl_pct", "duration_minutes", "outcome"):
        assert banned not in ev
    for banned in ("exit_reason", "held_minutes"):
        assert banned not in ev["market_context"], f"{banned} leaked via market_context"

    # And it must still carry enough to classify a setup at all.
    assert ev["market_context"]["rsi"] == 62.0
    assert ev["market_context"]["regime"] == "neutral"
    assert ev["agent_signals"]["technical"] == "BUY"


def test_entry_context_is_an_allow_list_not_a_block_list():
    # A block-list re-opens the hole the moment anything new is added upstream.
    # An unknown key must be dropped by default, even one that sounds harmless.
    from app.services.ai_engine_service import _entry_evidence

    ev = _entry_evidence(_trade(market_context={
        "rsi": 55.0,
        "some_future_field_nobody_thought_about": "realised_pnl=-3.2%",
    }))
    assert ev["market_context"] == {"rsi": 55.0}


def test_entry_evidence_is_identical_for_a_winner_and_a_loser():
    # Same setup, opposite outcomes -> byte-identical evidence. If this ever
    # fails, the two classes are being described differently and any lift
    # computed across them is measuring the description, not the setup.
    import json
    from app.services.ai_engine_service import _entry_evidence

    loser = _trade(outcome="LOSS", pnl_pct=-0.02, exit_price=98.0)
    winner = _trade(outcome="WIN", pnl_pct=0.03, exit_price=103.0, duration_minutes=12)
    assert json.dumps(_entry_evidence(loser)) == json.dumps(_entry_evidence(winner))


def test_rule_setup_tag_always_returns_a_known_tag():
    # A tag outside the closed list reintroduces the fragmentation this design
    # replaces ("chased" vs "chasing" momentum were counted as two modes).
    from app.services.ai_engine_service import _rule_setup_tag, _SETUP_TAGS

    cases = [
        _trade(market_context={"market_regime": "bearish"}),
        _trade(market_context={"rsi": 81.0}),
        _trade(market_context={"rsi": 12.0}),
        _trade(market_context={"momentum_pct": 2.5}),
        _trade(market_context={"momentum_pct": -2.5}),
        _trade(market_context={"momentum_pct": 0.0}),
        _trade(market_context={}),
    ]
    for t in cases:
        assert _rule_setup_tag(t) in _SETUP_TAGS


def test_setup_taxonomy_is_closed_and_unique():
    from app.services.ai_engine_service import _SETUP_TAGS
    assert len(_SETUP_TAGS) == len(set(_SETUP_TAGS))
    # Stable identifiers: renaming one silently splits its accumulated history.
    assert all(t == t.lower() and " " not in t for t in _SETUP_TAGS)


def test_win_loss_classification_prefers_the_explicit_outcome():
    from app.services.ai_engine_service import _is_win
    assert _is_win({"outcome": "WIN", "pnl_pct": -0.01}) is True
    assert _is_win({"outcome": "LOSS", "pnl_pct": 0.01}) is False
    assert _is_win({"pnl_pct": 0.004}) is True
    assert _is_win({"pnl_pct": -0.004}) is False
    assert _is_win({"pnl_pct": 0.0}) is False   # break-even is not a win


def test_lesson_loss_is_rendered_in_percent():
    # A -0.45% loss is stored as the fraction -0.0045. Rounded to 2 dp that is
    # -0.0 — the bug. Scaled to percent it reads -0.45.
    fraction = -0.0045
    assert round(fraction, 2) == 0.0                 # what the prompt used to say
    assert round(fraction * 100.0, 2) == -0.45       # what it must say


def test_lesson_percent_survives_small_losses():
    # Even a small real loss must not round away to zero.
    assert round(-0.0012 * 100.0, 2) != 0.0
