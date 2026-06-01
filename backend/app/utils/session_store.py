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

_KEY      = "live_session:{}"
_INDEX    = "live_sessions:index"
_TTL      = 60 * 60 * 36          # keep finished sessions ~36h for review


def _k(session_id: str) -> str:
    return _KEY.format(session_id)


async def save_session(s: dict) -> None:
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        await r.set(_k(s["id"]), json.dumps(s), ex=_TTL)
        await r.sadd(_INDEX, s["id"])
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
