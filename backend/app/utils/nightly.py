"""Once-a-day scheduling that survives the box being switched off overnight.

Every nightly learning loop used to be written the same way::

    while True:
        await asyncio.sleep(_seconds_until_hour_ist(HOUR))
        await do_the_work()

which silently assumes the process is alive at HOUR. On this deployment it is
not. The stack runs on a laptop that is powered down for the night, and every
nightly loop was scheduled into exactly the dead window:

    01:00 IST  pattern-model retrain
    02:00 IST  pattern-memory sweep
    03:00 IST  GBM retrain, loss post-mortems, ES log retention
    04:00 IST  LLM rejection review

Measured 2026-09-01: the pattern-model trainer had fired on **5 of the 15
nights** since it was added (17, 18, 19, 20 and 24 August, all at 19:30 UTC),
and all six loops last completed on the single night of 24->25 August. Seven
consecutive nights were missed. Nothing reported it, because a task sleeping
on a timer that never expires looks identical to a healthy one — and the
`asyncio.sleep` is simply discarded when the container stops, so there is no
catch-up on the next boot either.

The one loop that stayed healthy through all of it is the counterfactual
labeller, and the only thing it does differently is poll on an interval
(`_SWEEP_EVERY = 900`) rather than sleep to a wall-clock hour.

So this module reframes the contract. `hour_ist` stops meaning "fire at this
instant" and starts meaning "this day's work becomes due at this hour". The
driver wakes every `poll_secs`, asks whether the current due-day has been
satisfied, and runs if it has not — at 01:00 sharp when the machine is up,
or at 09:20 the next morning when it is not. Completion is recorded in
Postgres keyed by the due-day it satisfied, so:

  * a run happens at most once per IST day, no matter how often we restart;
  * a missed night is picked up on the next boot rather than skipped forever;
  * the record is durable across container recreation (Redis RDB can lose the
    last minutes on an abrupt power-off, which is the exact failure mode here);
  * `/api/monitor/snapshot` can finally show these loops, including the three
    it never tracked at all (memory sweep, loss learning, rejection review).

Catch-up runs are serialised through one process-wide lock. On a cold boot all
six loops are overdue at once, and they are heavy — a 400-symbol pattern
retrain and a 250-symbol GBM retrain alongside LLM post-mortems would other-
wise land together on an 11GB VM during market hours. The startup stagger is
ordered by `hour_ist` so the queue drains in the same order the original clock
schedule implied (sweep before GBM, post-mortems before the rejection review
that scores them).

Only success advances `last_slot`. A failed run records itself in the error
columns and leaves the slot outstanding, so a transient failure is retried on
the next poll instead of costing the day.

A run whose *inputs* are not ready yet should raise `NotReady` rather than
return. Catch-up reorders these loops against each other: on 2026-09-01 the
rejection review's catch-up fired at 09:20 IST, but it can only score decisions
the counterfactual labeller has labelled, and that labeller refuses to touch a
day until the day is over and only sweeps outside market hours. It found zero
rows, "succeeded", consumed the slot, and would never have looked again — so
2026-08-31's rejections would have gone unreviewed forever. `NotReady` leaves
the slot outstanding without recording a failure, and the poll picks it up once
the dependency lands.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class NotReady(Exception):
    """Raised by a run when its inputs are not available yet.

    Leaves the day's slot outstanding and records nothing — this is neither a
    success nor a failure, just "ask again later". Use it for a dependency
    another loop produces, not for a genuine error.

    It is also the right answer when a run COMPLETED but did no work because
    its data source was unreachable. Returning a `{"status": "no_data"}` dict
    reads as success here, advances `last_slot`, and spends the day: measured
    2026-09-08, the boot catch-up fired at 09:36 IST while the host's network
    was still settling after three days off, all 304 symbol fetches failed in
    3.1 seconds, and `pattern_autotrain` recorded a successful run that trained
    nothing. The same boot did it to the GBM retrain and the memory sweep. By
    the time the network was up an hour later the day was already spent, and
    the System Map showed a model 88h stale next to a loop row saying OK —
    both true, which is the confusing part. Deferring instead means the next
    5-minute poll picks the day up the moment the data is reachable.
    """


# One catch-up at a time. All of these loops live in the same process (the
# runner role), so a plain asyncio lock is the whole story.
_RUN_LOCK = asyncio.Lock()

_SCHEMA_LOCK = asyncio.Lock()
_schema_ready = False

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS nightly_run_state (
        name          TEXT PRIMARY KEY,
        last_slot     DATE,
        last_run_at   TIMESTAMPTZ,
        duration_secs DOUBLE PRECISION,
        detail        TEXT,
        last_error_at TIMESTAMPTZ,
        last_error    TEXT
    )
    """,
)


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with _SCHEMA_LOCK:
        if _schema_ready:
            return
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))
        _schema_ready = True


def due_slot(now_ist: datetime, hour_ist: int) -> date:
    """The IST date of the most recent `hour_ist` boundary.

    At 09:20 on Sep 1 with hour_ist=1 the 01:00 slot for Sep 1 has already
    opened, so Sep 1 is due. At 00:40 the same day it has not, so the
    outstanding slot is still Aug 31's.
    """
    if now_ist.hour >= hour_ist % 24:
        return now_ist.date()
    return (now_ist - timedelta(days=1)).date()


async def last_slot(name: str) -> Optional[date]:
    """The most recent due-day this loop has satisfied, or None if never."""
    from sqlalchemy import text
    from app.database.postgres import engine
    await _ensure_schema()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT last_slot FROM nightly_run_state WHERE name = :n"),
            {"n": name},
        )).fetchone()
    return row[0] if row else None


async def _mark_ok(name: str, slot: date, duration: float, detail: str) -> None:
    from sqlalchemy import text
    from app.database.postgres import engine
    await _ensure_schema()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO nightly_run_state
                   (name, last_slot, last_run_at, duration_secs, detail)
            VALUES (:n, :s, now(), :d, :dt)
            ON CONFLICT (name) DO UPDATE SET
                   last_slot = EXCLUDED.last_slot,
                   last_run_at = EXCLUDED.last_run_at,
                   duration_secs = EXCLUDED.duration_secs,
                   detail = EXCLUDED.detail
        """), {"n": name, "s": slot, "d": round(duration, 1), "dt": detail[:500]})


async def _mark_error(name: str, err: str) -> None:
    """Record a failure WITHOUT advancing last_slot or last_run_at — the slot
    stays outstanding so the next poll retries it, and the monitor keeps
    reporting the age of the last run that actually produced something."""
    from sqlalchemy import text
    from app.database.postgres import engine
    await _ensure_schema()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO nightly_run_state (name, last_error_at, last_error)
            VALUES (:n, now(), :e)
            ON CONFLICT (name) DO UPDATE SET
                   last_error_at = EXCLUDED.last_error_at,
                   last_error = EXCLUDED.last_error
        """), {"n": name, "e": err[:500]})


async def states() -> dict[str, dict]:
    """Every loop's last run, for the monitor. Never raises."""
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        await _ensure_schema()
        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT name, last_slot, last_run_at, duration_secs, detail, "
                "       last_error_at, last_error "
                "FROM nightly_run_state"
            ))).fetchall()
        return {r[0]: {"last_slot": r[1], "last_run_at": r[2], "duration_secs": r[3],
                       "detail": r[4], "last_error_at": r[5], "last_error": r[6]}
                for r in rows}
    except Exception as exc:
        logger.warning("nightly state read failed: %s", exc)
        return {}


async def nightly_loop(
    name: str,
    hour_ist: int,
    run: Callable[[], Awaitable[object]],
    *,
    label: Optional[str] = None,
    poll_secs: float = 300.0,
    stagger: bool = True,
) -> None:
    """Drive `run` at most once per IST day, from `hour_ist` onwards.

    `run` is awaited with no arguments; whatever it returns is logged.
    """
    what = label or name

    # Ordered by the hour each loop was originally scheduled for, so a cold
    # boot drains the backlog in the intended dependency order rather than
    # whichever coroutine happens to reach the lock first.
    if stagger:
        await asyncio.sleep(min(hour_ist % 24, 12) * 20.0)

    logger.info("%s: due daily from %02d:00 IST, catch-up enabled", what, hour_ist % 24,
                extra={"log_type": "app_lifecycle", "event": "nightly_scheduled",
                       "loop": name, "hour_ist": hour_ist % 24})

    while True:
        sleep_for = poll_secs
        try:
            slot = due_slot(datetime.now(IST), hour_ist)
            done = await last_slot(name)

            if done is None or done < slot:
                behind = "(never run)" if done is None else f"(last slot {done})"
                # "queued", not "running": on a cold boot all six loops are due
                # at once and each waits its turn on _RUN_LOCK, which on this
                # box means hours behind a 400-symbol retrain. Saying "running"
                # here would make the log lie about what is on the CPU.
                logger.info("%s: slot %s is due %s — queued", what, slot, behind,
                            extra={"log_type": "app_lifecycle", "event": "nightly_due",
                                   "loop": name, "slot": str(slot)})

                async with _RUN_LOCK:
                    # Re-check under the lock: a queued loop may have waited
                    # hours behind a long retrain and rolled past its own slot
                    # boundary, or another worker may have satisfied it.
                    slot = due_slot(datetime.now(IST), hour_ist)
                    done = await last_slot(name)
                    if done is None or done < slot:
                        logger.info("%s: slot %s starting", what, slot,
                                    extra={"log_type": "app_lifecycle",
                                           "event": "nightly_start",
                                           "loop": name, "slot": str(slot)})
                        started = datetime.now(timezone.utc)
                        try:
                            res = await run()
                        except NotReady as exc:
                            # How long the deferral cost decides whether it is
                            # worth repeating. A run that gave up in seconds gave
                            # up because nothing was reachable, and the next poll
                            # costs those same few seconds — cheap to keep asking.
                            # A run that ground for longer than the gap between
                            # polls and STILL had nothing is not a transient
                            # network gap; deferring it would put a heavy job back
                            # on an 11GB VM continuously, market hours included.
                            # Use poll_secs as the line rather than a constant:
                            # the question is exactly whether the attempt costs
                            # more than the interval between attempts.
                            secs = (datetime.now(timezone.utc) - started).total_seconds()
                            if secs <= poll_secs:
                                raise
                            await _mark_ok(
                                name, slot, secs,
                                f"not ready after {secs:.0f}s ({exc}) — slot consumed, "
                                f"too slow to retry every {poll_secs:.0f}s")
                            logger.warning(
                                "%s: slot %s not ready after %.0fs (%s) — consuming the "
                                "slot rather than retrying a job that outlasts the poll",
                                what, slot, secs, exc,
                                extra={"log_type": "app_lifecycle",
                                       "event": "nightly_defer_too_slow", "loop": name,
                                       "slot": str(slot), "duration_secs": secs})
                        else:
                            secs = (datetime.now(timezone.utc) - started).total_seconds()
                            await _mark_ok(name, slot, secs, str(res))
                            logger.info("%s: slot %s done in %.0fs: %s", what, slot, secs, res,
                                        extra={"log_type": "app_lifecycle",
                                               "event": "nightly_done", "loop": name,
                                               "slot": str(slot), "duration_secs": secs})

        except asyncio.CancelledError:
            break
        except NotReady as exc:
            # Slot stays outstanding, nothing recorded. Debug level: on a cold
            # boot this can be true for hours and it is not a fault.
            logger.debug("%s: not ready yet (%s) — will retry in %.0fs",
                         what, exc, poll_secs,
                         extra={"log_type": "app_lifecycle", "event": "nightly_deferred",
                                "loop": name, "slot": str(slot)})
        except Exception as exc:
            logger.error("%s: run failed: %s", what, exc,
                         extra={"log_type": "app_lifecycle", "event": "nightly_error",
                                "loop": name})
            try:
                await _mark_error(name, str(exc))
            except Exception:
                pass
            # Back off, but not past the poll cadence by much — the slot is
            # still outstanding and we do want the day's run.
            sleep_for = min(3600.0, poll_secs * 6)

        try:
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            break
