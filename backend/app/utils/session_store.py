"""Redis-backed store for live trading sessions.

A session is the server-side state of a running AI trade (replay or paper):
cash, position, trades, candles-so-far, last agent decision, status. Storing it
server-side is what lets a session survive a browser refresh, keep advancing in
the background, run alongside other sessions, and be reopened later.

Layout:
  live_session:{id}   → JSON blob of the full session
  live_sessions:index → SET of all session ids
"""
from __future__ import annotations
import json
from typing import Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_KEY          = "live_session:{}"
_INDEX        = "live_sessions:index"
_RUNNING_IDX  = "live_sessions:running"   # only sessions currently in status=running
_TTL          = 60 * 60 * 36              # keep finished sessions ~36h for review


def _k(session_id: str) -> str:
    return _KEY.format(session_id)


async def save_session(s: dict) -> None:
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        await r.set(_k(s["id"]), json.dumps(s), ex=_TTL)
        await r.sadd(_INDEX, s["id"])
        # Maintain a separate set of only running session IDs so the background
        # runner loop can poll cheaply without loading all finished sessions.
        if s.get("status") == "running":
            await r.sadd(_RUNNING_IDX, s["id"])
            await r.expire(_RUNNING_IDX, _TTL)
        else:
            await r.srem(_RUNNING_IDX, s["id"])
    except Exception as exc:
        logger.warning("save_session failed: %s", exc)


async def get_session(session_id: str) -> Optional[dict]:
    from app.utils.redis_client import get_redis
    try:
        raw = await get_redis().get(_k(session_id))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("get_session failed: %s", exc)
        return None


async def delete_session(session_id: str) -> None:
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        await r.delete(_k(session_id))
        await r.srem(_INDEX, session_id)
    except Exception as exc:
        logger.warning("delete_session failed: %s", exc)


async def list_ids() -> list[str]:
    from app.utils.redis_client import get_redis
    try:
        return list(await get_redis().smembers(_INDEX))
    except Exception as exc:
        logger.warning("list_ids failed: %s", exc)
        return []


async def list_sessions() -> list[dict]:
    """Return all sessions (full blobs), pruning index entries that have expired."""
    from app.utils.redis_client import get_redis
    ids = await list_ids()
    out: list[dict] = []
    stale: list[str] = []
    for sid in ids:
        s = await get_session(sid)
        if s is None:
            stale.append(sid)
        else:
            out.append(s)
    if stale:
        try:
            await get_redis().srem(_INDEX, *stale)
        except Exception:
            pass
    out.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return out


async def list_running_sessions() -> list[dict]:
    """Fast path for the background runner — loads only sessions with status=running.

    Uses a separate Redis set (_RUNNING_IDX) that is maintained in sync by
    save_session(). On first call after deploy (or if the set is missing), falls
    back to a full scan and rebuilds the running index from what it finds.
    """
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        ids = list(await r.smembers(_RUNNING_IDX))
    except Exception:
        ids = []

    if not ids:
        # Running index is empty or missing — full scan once to rebuild it.
        all_sessions = await list_sessions()
        running = [s for s in all_sessions if s.get("status") == "running"]
        if running:
            try:
                r = get_redis()
                await r.sadd(_RUNNING_IDX, *[s["id"] for s in running])
                await r.expire(_RUNNING_IDX, _TTL)
            except Exception:
                pass
        return running

    out: list[dict] = []
    stale: list[str] = []
    for sid in ids:
        s = await get_session(sid)
        if s is None or s.get("status") != "running":
            stale.append(sid)
        else:
            out.append(s)
    if stale:
        try:
            await get_redis().srem(_RUNNING_IDX, *stale)
        except Exception:
            pass
    return out
