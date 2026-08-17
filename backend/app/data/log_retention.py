"""Elasticsearch log retention — automatic ageing-out plus manual purges.

Both log indices are daily (`neuradex-logs-YYYY.MM.DD` from the app's
elk_logger, `neuradex-docker-YYYY.MM.DD` from Filebeat), which makes retention a
matter of DELETING WHOLE INDICES rather than running delete-by-query. That
matters: delete-by-query rewrites segments and leaves the space occupied until a
merge, while dropping an index frees it immediately and costs almost nothing.

The retention window lives in Redis so it can be changed from the System Map
without a restart, falling back to LOG_RETENTION_DAYS and then to a default.

Deliberately conservative:
  * Only indices matching the exact `<prefix>-YYYY.MM.DD` shape are considered.
    Anything else (`neuradex-logs-test`, a hand-made index, an alias) is left
    alone — an unparseable name must never be read as "old".
  * Today's index is never dropped, whatever the window says.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
_PREFIXES = ("neuradex-logs", "neuradex-docker")

# Only `prefix-YYYY.MM.DD`. Anything else is not a dated daily index.
_DATED_RE = re.compile(r"^(neuradex-(?:logs|docker))-(\d{4})\.(\d{2})\.(\d{2})$")

_REDIS_KEY = "monitor:log_retention_days"
_DEFAULT_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "14"))
_MIN_DAYS = 1
_MAX_DAYS = 365

_RUN_HOUR_IST = int(os.getenv("LOG_RETENTION_HOUR_IST", "03"))
_RUN_MINUTE_IST = int(os.getenv("LOG_RETENTION_MINUTE_IST", "20"))


async def get_retention_days() -> int:
    """Effective window: Redis override → env default."""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_REDIS_KEY)
        if raw:
            return max(_MIN_DAYS, min(_MAX_DAYS, int(raw)))
    except Exception:
        logger.debug("log retention read failed; using default", exc_info=True)
    return _DEFAULT_DAYS


async def set_retention_days(days: int) -> int:
    days = max(_MIN_DAYS, min(_MAX_DAYS, int(days)))
    from app.utils.redis_client import cache_set
    # No TTL: this is a setting, not a cache entry.
    await cache_set(_REDIS_KEY, str(days), expire=None)
    logger.info("log retention set to %d days", days,
                extra={"log_type": "log_retention", "event": "window_set", "days": days})
    return days


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


async def _indices() -> list[dict]:
    """Every dated log index with its doc count and size."""
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            # `bytes=b` makes _cat report store.size as a raw integer. There is
            # no `store.size_in_bytes` COLUMN — asking for one yields an empty
            # field and every size reads as 0.
            r = await c.get(f"{_ES_URL}/_cat/indices/neuradex-*",
                            params={"format": "json", "bytes": "b",
                                    "h": "index,docs.count,store.size"})
            r.raise_for_status()
            rows = r.json()
    except Exception as exc:
        logger.warning("could not list log indices: %s", exc)
        return out

    for row in rows:
        name = row.get("index", "")
        m = _DATED_RE.match(name)
        out.append({
            "index": name,
            "prefix": m.group(1) if m else None,
            "date": f"{m.group(2)}-{m.group(3)}-{m.group(4)}" if m else None,
            "dated": bool(m),
            "docs": int(row.get("docs.count") or 0),
            "bytes": int(row.get("store.size") or 0),
            "size": _human(int(row.get("store.size") or 0)),
        })
    out.sort(key=lambda i: i["index"])
    return out


async def stats() -> dict:
    idx = await _indices()
    days = await get_retention_days()
    cutoff = (datetime.now(IST) - timedelta(days=days)).date()
    today = datetime.now(IST).date()

    expiring = [
        i for i in idx
        if i["dated"] and datetime.strptime(i["date"], "%Y-%m-%d").date() < cutoff
        and datetime.strptime(i["date"], "%Y-%m-%d").date() != today
    ]
    return {
        "retentionDays": days,
        "indices": idx,
        "totalIndices": len(idx),
        "totalDocs": sum(i["docs"] for i in idx),
        "totalBytes": sum(i["bytes"] for i in idx),
        "expiringCount": len(expiring),
        "expiringBytes": sum(i["bytes"] for i in expiring),
        "cutoff": cutoff.isoformat(),
    }


async def _delete(names: list[str]) -> dict:
    if not names:
        return {"deleted": [], "freedBytes": 0}
    idx = {i["index"]: i for i in await _indices()}
    freed = sum(idx.get(n, {}).get("bytes", 0) for n in names)
    deleted: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for name in names:
            try:
                r = await c.delete(f"{_ES_URL}/{name}")
                if r.status_code in (200, 404):
                    deleted.append(name)
                else:
                    logger.warning("index delete %s -> %s", name, r.status_code)
            except Exception as exc:
                logger.warning("index delete %s failed: %s", name, exc)
    return {"deleted": deleted, "freedBytes": freed}


async def prune(days: int | None = None, dry_run: bool = False) -> dict:
    """Drop dated indices older than the retention window."""
    days = days if days is not None else await get_retention_days()
    cutoff = (datetime.now(IST) - timedelta(days=days)).date()
    today = datetime.now(IST).date()

    victims = []
    for i in await _indices():
        if not i["dated"]:
            continue                       # never touch un-dated indices
        d = datetime.strptime(i["date"], "%Y-%m-%d").date()
        if d < cutoff and d != today:      # today's index is always kept
            victims.append(i["index"])

    if dry_run:
        return {"wouldDelete": victims, "count": len(victims),
                "cutoff": cutoff.isoformat(), "dryRun": True}

    res = await _delete(victims)
    if res["deleted"]:
        logger.info("log retention pruned %d indices older than %s",
                    len(res["deleted"]), cutoff,
                    extra={"log_type": "log_retention", "event": "pruned",
                           "count": len(res["deleted"])})
    return {**res, "count": len(res["deleted"]), "cutoff": cutoff.isoformat(),
            "dryRun": False}


async def purge_all(keep_today: bool = True) -> dict:
    """Clear the log store. `keep_today=False` also drops the live index, which
    is what "clear everything" means to an operator staring at a noisy panel."""
    today = datetime.now(IST).date().isoformat()
    victims = [
        i["index"] for i in await _indices()
        if i["dated"] and not (keep_today and i["date"] == today)
    ]
    res = await _delete(victims)
    logger.warning("log store purged: %d indices removed (keep_today=%s)",
                   len(res["deleted"]), keep_today,
                   extra={"log_type": "log_retention", "event": "purge_all"})
    return {**res, "count": len(res["deleted"])}


async def purge_service(service: str) -> dict:
    """Remove one service's documents across all log indices.

    This one HAS to be delete-by-query — a service's lines are interleaved with
    every other service's inside the same daily index, so there is no index to
    drop. Space is reclaimed on the next merge rather than immediately.
    """
    body = {"query": {"bool": {"should": [
        {"match_phrase": {"service": service}},
        {"term": {"service": service}},
    ], "minimum_should_match": 1}}}
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{_ES_URL}/neuradex-*/_delete_by_query",
                             params={"conflicts": "proceed"}, json=body)
            r.raise_for_status()
            js = r.json()
        logger.warning("purged %s log documents for service=%s",
                       js.get("deleted"), service,
                       extra={"log_type": "log_retention", "event": "purge_service",
                              "service_purged": service})
        return {"service": service, "deleted": js.get("deleted", 0)}
    except Exception as exc:
        logger.warning("purge_service(%s) failed: %s", service, exc)
        return {"service": service, "deleted": 0, "error": str(exc)[:160]}


def _seconds_until_run() -> float:
    now = datetime.now(IST)
    target = now.replace(hour=_RUN_HOUR_IST, minute=_RUN_MINUTE_IST,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def log_retention_loop() -> None:
    """Nightly index pruning. Runs in the runner/full role only."""
    logger.info("log retention scheduled — %02d:%02d IST, default window %d days",
                _RUN_HOUR_IST, _RUN_MINUTE_IST, _DEFAULT_DAYS,
                extra={"log_type": "log_retention", "event": "scheduled"})
    while True:
        try:
            await asyncio.sleep(_seconds_until_run())
            await prune()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("log retention run failed: %s", exc)
            await asyncio.sleep(3600)
