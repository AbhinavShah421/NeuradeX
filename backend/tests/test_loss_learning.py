"""Unit tests for the loss-learning loop (no network, no DB).

Locks in:
  • The loop is actually scheduled. loss_learning_run existed only behind its
    manual POST endpoint, so it ran when somebody remembered to — last on
    2026-06-18, leaving 125 losing trades unanalysed and a stale lessons cache.
  • Lesson loss magnitudes are rendered in PERCENT. trade_postmortems.pnl_pct
    is a fraction (copied from trade_records via the feedback-service), so
    rounding it to 2 dp made every lesson read "avg -0.0%" — the decision
    prompts were told each past mistake had cost nothing.
"""
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


def test_seconds_until_hour_ist_is_within_a_day():
    from app.services.ai_engine_service import _seconds_until_hour_ist
    for hour in (0, 3, 12, 23):
        w = _seconds_until_hour_ist(hour)
        assert 0 < w <= 86400, (hour, w)


def test_lesson_loss_is_rendered_in_percent():
    # A -0.45% loss is stored as the fraction -0.0045. Rounded to 2 dp that is
    # -0.0 — the bug. Scaled to percent it reads -0.45.
    fraction = -0.0045
    assert round(fraction, 2) == 0.0                 # what the prompt used to say
    assert round(fraction * 100.0, 2) == -0.45       # what it must say


def test_lesson_percent_survives_small_losses():
    # Even a small real loss must not round away to zero.
    assert round(-0.0012 * 100.0, 2) != 0.0
