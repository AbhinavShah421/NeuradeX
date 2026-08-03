"""
Retention for session_decisions.

The table is append-only and unbounded: every candle of every session writes a
row carrying its full agent vote array. Backtest bursts dominate it — a single
sweep in late July put ~238k rows in, against ~59k from five weeks of live paper
trading. Left alone it grows without limit.

Nothing here is urgent for memory: postgres holds the table almost entirely as
reclaimable page cache (anon RSS is single-digit MB). This is about disk.

What may be deleted is decided by what still reads a row:

  • counterfactual.label_pending() only considers rows created within
    _LABEL_BATCH_DAYS + 4 = 7 days. Past that a row with no cf label can never
    get one, and every learning query filters on `cf_pnl_pct IS NOT NULL`, so
    it is dead weight the moment it ages out. Deleted at 10 days for margin.

  • learning.py and llm_rejection_review.py aggregate cf-labeled rows with no
    time bound — that is the learning corpus, so labeled rows are kept far
    longer, and paper rows longest of all since a backtest can be re-run but a
    live session cannot be replayed.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Must stay comfortably above counterfactual._LABEL_BATCH_DAYS + 4 (=7), or this
# deletes rows the labeler was still going to reach.
UNLABELABLE_DAYS = int(os.getenv("RETENTION_UNLABELABLE_DAYS", "10"))
# Backtest and replay decisions are reproducible — re-running the sweep rebuilds
# them — so they need not be kept as long as live observations.
BACKTEST_DAYS = int(os.getenv("RETENTION_BACKTEST_DAYS", "45"))
# Paper decisions record real sessions that cannot be regenerated.
PAPER_DAYS = int(os.getenv("RETENTION_PAPER_DAYS", "180"))

# Delete in chunks so the table is never locked for long.
_BATCH = 20_000
_RUN_HOUR_IST = int(os.getenv("RETENTION_HOUR_IST", "16"))
_RUN_MINUTE_IST = int(os.getenv("RETENTION_MINUTE_IST", "30"))

_RULES: tuple[tuple[str, str], ...] = (
    (
        "unlabelable",
        """cf_pnl_pct IS NULL
           AND created_at < now() - make_interval(days => :unlabelable_days)""",
    ),
    (
        "backtest_replay",
        """cf_pnl_pct IS NOT NULL
           AND created_at < now() - make_interval(days => :backtest_days)
           AND session_id IN (SELECT session_id FROM session_metadata
                              WHERE mode IN ('backtest', 'replay'))""",
    ),
    (
        "paper",
        """cf_pnl_pct IS NOT NULL
           AND created_at < now() - make_interval(days => :paper_days)
           AND session_id IN (SELECT session_id FROM session_metadata
                              WHERE mode = 'paper')""",
    ),
)


def _params() -> dict:
    return {
        "unlabelable_days": UNLABELABLE_DAYS,
        "backtest_days": BACKTEST_DAYS,
        "paper_days": PAPER_DAYS,
    }


async def prune_session_decisions(dry_run: bool = False) -> dict:
    """Apply every retention rule. With dry_run, only count what would go."""
    from app.database.postgres import engine

    result: dict = {"dry_run": dry_run, "deleted": {}, "total": 0}

    for name, predicate in _RULES:
        try:
            if dry_run:
                async with engine.begin() as conn:
                    n = (await conn.execute(
                        text(f"SELECT count(*) FROM session_decisions WHERE {predicate}"),
                        _params(),
                    )).scalar() or 0
                result["deleted"][name] = int(n)
                result["total"] += int(n)
                continue

            removed = 0
            while True:
                # ctid keeps each statement bounded regardless of how far
                # behind retention has fallen.
                async with engine.begin() as conn:
                    n = (await conn.execute(
                        text(f"""
                            DELETE FROM session_decisions
                             WHERE ctid IN (SELECT ctid FROM session_decisions
                                             WHERE {predicate} LIMIT {_BATCH})
                        """),
                        _params(),
                    )).rowcount or 0
                removed += n
                if n < _BATCH:
                    break
                await asyncio.sleep(0.5)   # let concurrent writers through

            result["deleted"][name] = removed
            result["total"] += removed
            if removed:
                logger.info(
                    "Retention removed %d session_decisions rows (%s)", removed, name,
                    extra={"log_type": "retention", "event": "pruned",
                           "rule": name, "rows": removed},
                )
        except Exception as exc:
            logger.error(
                "Retention rule %s failed: %s", name, exc,
                extra={"log_type": "retention", "event": "rule_failed", "rule": name},
            )
            result["deleted"][name] = f"error: {exc}"

    if result["total"] and not dry_run:
        # Deleted space is reusable but not returned to the OS; ANALYZE at least
        # keeps the planner honest about what is left. A VACUUM FULL would
        # reclaim it but needs an exclusive lock, so that stays manual.
        try:
            async with engine.begin() as conn:
                await conn.execute(text("ANALYZE session_decisions"))
        except Exception as exc:
            logger.warning("Retention ANALYZE failed: %s", exc)

    return result


def _seconds_until_run() -> float:
    now = datetime.now(IST)
    target = now.replace(hour=_RUN_HOUR_IST, minute=_RUN_MINUTE_IST,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def retention_loop() -> None:
    """Prune once a day, after the close so no session is mid-write."""
    logger.info(
        "session_decisions retention scheduled — %02d:%02d IST "
        "(unlabelable %dd, backtest/replay %dd, paper %dd)",
        _RUN_HOUR_IST, _RUN_MINUTE_IST, UNLABELABLE_DAYS, BACKTEST_DAYS, PAPER_DAYS,
        extra={"log_type": "retention", "event": "scheduled"},
    )
    while True:
        try:
            await asyncio.sleep(_seconds_until_run())
            res = await prune_session_decisions()
            logger.info(
                "session_decisions retention pass removed %d rows", res["total"],
                extra={"log_type": "retention", "event": "pass_done", **res["deleted"]},
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Retention loop error: %s", exc,
                         extra={"log_type": "retention", "event": "loop_error"})
            await asyncio.sleep(3600)
