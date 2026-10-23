"""A nightly run that did no work must not book the day.

`nightly_loop` treats a returned value as success and advances `last_slot`, so
a trainer handing back `{"status": "no_data"}` spends a day it never used. That
happened on 2026-09-08: the boot catch-up beat the host's network back up after
three days off, every candle fetch failed in milliseconds, and the pattern
trainer, the GBM retrain and the memory sweep all recorded successful runs over
zero samples. The providers served normally an hour later; none of the three
looked again until the next day.

These tests pin the two halves of the fix — the guards that raise `NotReady`,
and the loop's bound on how long a deferral may cost before it is cheaper to
accept the empty day.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.agents.pattern_model import _defer_if_untrained
from app.utils import nightly
from app.utils.nightly import NotReady, due_slot


# ── the guards ───────────────────────────────────────────────────────────────

def test_no_data_defers_instead_of_booking_the_day():
    with pytest.raises(NotReady):
        _defer_if_untrained({"status": "no_data", "symbols": 304}, "Pattern model")


def test_already_running_defers():
    """A manual retrain mid-flight did this call's work; crediting the schedule
    with it would consume the slot for a run that did nothing."""
    with pytest.raises(NotReady):
        _defer_if_untrained({"status": "already_running"}, "GBM")


def test_a_real_training_run_is_left_alone():
    _defer_if_untrained({"status": "ok", "samples": 41000}, "Pattern model")


def test_unrecognised_results_are_left_alone():
    """The guard only knows two no-op shapes. Anything else — including a
    non-dict — is the trainer's business, not the scheduler's."""
    _defer_if_untrained({"status": "skipped_frozen"}, "sweep")
    _defer_if_untrained("done", "sweep")


# ── the loop's response ──────────────────────────────────────────────────────

class _FakeState:
    """Stands in for nightly_run_state so the loop can run without Postgres."""

    def __init__(self, slot=None):
        self.slot = slot
        self.marked: list[tuple] = []
        self.errors: list[str] = []


@pytest.fixture
def state(monkeypatch):
    st = _FakeState()

    async def _last_slot(name):
        return st.slot

    async def _mark_ok(name, slot, duration, detail):
        st.slot = slot
        st.marked.append((slot, duration, detail))

    async def _mark_error(name, err):
        st.errors.append(err)

    monkeypatch.setattr(nightly, "last_slot", _last_slot)
    monkeypatch.setattr(nightly, "_mark_ok", _mark_ok)
    monkeypatch.setattr(nightly, "_mark_error", _mark_error)
    return st


async def _run_one_poll(run, state, poll_secs=300.0):
    """Drive nightly_loop through a single poll, then stop it.

    The loop is an infinite `while True`; cancelling it once the run has been
    attempted is the honest way to observe one iteration.
    """
    task = asyncio.create_task(
        nightly.nightly_loop("test_loop", 1, run, poll_secs=poll_secs, stagger=False)
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if state.marked or getattr(run, "calls", 0):
            break
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_fast_deferral_leaves_the_slot_outstanding(state):
    """The 2026-09-08 case: nothing reachable, gives up in milliseconds. The day
    must stay unclaimed so the next poll can take it once the network is up."""

    async def run():
        run.calls += 1
        raise NotReady("no candle data reached it")
    run.calls = 0

    await _run_one_poll(run, state)

    assert run.calls >= 1
    assert state.marked == [], "a deferral must not record a run"
    assert state.errors == [], "a deferral is not a failure either"
    assert state.slot is None, "the slot must stay outstanding for the next poll"


@pytest.mark.asyncio
async def test_a_deferral_slower_than_the_poll_consumes_the_slot(state, monkeypatch):
    """A run that grinds longer than the gap between polls and still has nothing
    is not a transient network gap. Retrying it would mean it is always running,
    on a box that also has to trade — so the empty day is accepted instead."""
    clock = {"t": datetime(2026, 9, 8, 4, 0, tzinfo=timezone.utc)}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["t"].astimezone(tz) if tz else clock["t"]

    monkeypatch.setattr(nightly, "datetime", _Clock)

    async def run():
        run.calls += 1
        clock["t"] += timedelta(seconds=3000)      # ten times the poll interval
        raise NotReady("still nothing after a long grind")
    run.calls = 0

    await _run_one_poll(run, state, poll_secs=300.0)

    assert run.calls >= 1
    assert len(state.marked) == 1
    slot, duration, detail = state.marked[0]
    assert duration == pytest.approx(3000, abs=1)
    assert "slot consumed" in detail
    assert state.slot == slot


@pytest.mark.asyncio
async def test_a_productive_run_still_books_the_day(state):
    async def run():
        run.calls += 1
        return {"status": "ok", "samples": 41000}
    run.calls = 0

    await _run_one_poll(run, state)

    assert len(state.marked) == 1
    assert "ok" in state.marked[0][2]
    assert state.slot == due_slot(datetime.now(nightly.IST), 1)


@pytest.mark.asyncio
async def test_a_genuine_error_records_and_keeps_the_slot(state):
    """Unchanged behaviour, pinned here because the new NotReady branch sits
    directly beside it: a real exception is a failure, not a deferral."""

    async def run():
        run.calls += 1
        raise RuntimeError("postgres went away")
    run.calls = 0

    await _run_one_poll(run, state)

    assert state.errors and "postgres went away" in state.errors[0]
    assert state.marked == []
    assert state.slot is None


def test_due_slot_before_and_after_the_hour():
    """The day's work becomes due at the hour, so a 09:20 boot owes today's slot
    while a 00:40 one still owes yesterday's."""
    ist = nightly.IST
    assert due_slot(datetime(2026, 9, 8, 9, 20, tzinfo=ist), 1) == date(2026, 9, 8)
    assert due_slot(datetime(2026, 9, 8, 0, 40, tzinfo=ist), 1) == date(2026, 9, 7)
