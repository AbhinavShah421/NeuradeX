# Canonical source for every agent service's app/agent_bootstrap.py.
# Do not edit the per-service copies directly — edit this file, then run
# `python scripts/sync_shared_python.py` to propagate the change.
"""Shared FastAPI lifespan/health-check helpers for the agent services.

Every agent independently reimplemented the same "connect with exponential
backoff, retrying up to N times" loop for its Postgres pool / RabbitMQ
connection, with inconsistent behavior on exhaustion (some silently kept
running with a None connection forever, some crashed to force a container
restart). This module centralizes that pattern so each service just states
whether the dependency is required.
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def connect_with_retry(
    connect: Callable[[], Awaitable[T]],
    *,
    what: str,
    max_attempts: int = 10,
    required: bool = True,
) -> Optional[T]:
    """Retry an async connect callable with exponential backoff (capped at 30s).

    On success, returns the connected resource. On exhausting all attempts:
    raises RuntimeError if `required` (so the container restarts instead of
    running in a permanently-degraded state), otherwise logs and returns None
    for callers that already handle a missing resource gracefully.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = await connect()
            if attempt > 1:
                logger.info("%s connected on attempt %d/%d", what, attempt, max_attempts)
            return result
        except Exception as exc:
            delay = min(2 ** attempt, 30)
            logger.warning(
                "%s connect attempt %d/%d failed: %s — retrying in %ds",
                what, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    if required:
        raise RuntimeError(
            f"{what}: could not connect after {max_attempts} attempts — crashing for container restart"
        )
    logger.error("%s: could not connect after %d attempts — continuing without it", what, max_attempts)
    return None


def health_payload(service: str, **checks: object) -> dict:
    """Build a consistent /health response body.

    Pass extra checks as kwargs (e.g. db_pool=True, policy_loaded=False).
    Overall "status" becomes "degraded" if any boolean check is False.
    """
    status = "degraded" if any(v is False for v in checks.values()) else "ok"
    return {"status": status, "service": service, **checks}
