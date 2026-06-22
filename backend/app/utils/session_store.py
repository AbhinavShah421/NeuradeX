"""Redis-backed store for live trading sessions.

A session is the server-side state of a running AI trade (replay or paper):
cash, position, trades, candles-so-far, last agent decision, status. Storing it
server-side is what lets a session survive a browser refresh, keep advancing in
the background, run alongside other sessions, and be reopened later.

Layout:
  live_session:{id}         → JSON blob (control fields only — NO candle arrays)
  live_session_candles:{id} → JSON blob (all_candles + prev_day_candles, written
                               once at session creation, immutable thereafter)
  live_sessions:index       → SET of all session ids
  live_sessions:running     → SET of currently running session ids

Separating the candle arrays (37–74 KB per session) from the control blob
(~5 KB) means the runner loads only ~5 KB per session when scanning the running
index, saving several MB of Redis I/O per tick under heavy backtest load.
"""
from __future__ import annotations
import json
from typing import Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_KEY          = "live_session:{}"
_CANDLES_KEY  = "live_session_candles:{}"
_INDEX        = "live_sessions:index"
_RUNNING_IDX  = "live_sessions:running"
_TTL          = 60 * 60 * 8   # keep running sessions ~8h
_TTL_DONE     = 60 * 30        # keep finished sessions only 30 min

# Fields that are immutable after session creation and stored separately.
_CANDLE_FIELDS = frozenset(["all_candles", "prev_day_candles"])


def _k(session_id: str) -> str:
    return _KEY.format(session_id)


def _ck(session_id: str) -> str:
    return _CANDLES_KEY.format(session_id)


async def save_session(s: dict) -> None:
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        sid = s["id"]

        # Persist immutable candle arrays in a dedicated key — written once at
        # session creation, never re-written on subsequent saves (they don't
        # change during replay/backtest). This shrinks the hot-path control blob
        # from ~150 KB to ~5 KB, cutting per-tick Redis I/O by ~97%.
        candles_data = {k: s[k] for k in _CANDLE_FIELDS if k in s}
        if candles_data and not await r.exists(_ck(sid)):
            await r.set(_ck(sid), json.dumps(candles_data), ex=_TTL)

        # Slim control blob (no candle arrays).
        slim = {k: v for k, v in s.items() if k not in _CANDLE_FIELDS}
        _is_running = s.get("status") == "running"
        _ttl = _TTL if _is_running else _TTL_DONE
        await r.set(_k(sid), json.dumps(slim), ex=_ttl)
        await r.sadd(_INDEX, sid)
        if _is_running:
            await r.sadd(_RUNNING_IDX, sid)
            await r.expire(_RUNNING_IDX, _TTL)
        else:
            await r.srem(_RUNNING_IDX, sid)
    except Exception as exc:
        logger.warning("save_session failed: %s", exc)


async def get_session(session_id: str) -> Optional[dict]:
    """Load the full session dict (control fields + candle arrays merged back)."""
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        raw = await r.get(_k(session_id))
        if not raw:
            return None
        s = json.loads(raw)
        # Merge candle arrays back for callers that need them (runner advance,
        # detail endpoint). Extra Redis round-trip is worth it — callers that
        # don't need candles should use get_session_slim() instead.
        candles_raw = await r.get(_ck(session_id))
        if candles_raw:
            s.update(json.loads(candles_raw))
        return s
    except Exception as exc:
        logger.warning("get_session failed: %s", exc)
        return None


async def get_session_slim(session_id: str) -> Optional[dict]:
    """Load only the control blob — no candle arrays.  Fast path for the runner's
    stale-check and status scanning.  Callers must NOT use the result for
    _advance_replay (all_candles will be missing)."""
    from app.utils.redis_client import get_redis
    try:
        raw = await get_redis().get(_k(session_id))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("get_session_slim failed: %s", exc)
        return None


async def delete_session(session_id: str) -> None:
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        await r.delete(_k(session_id), _ck(session_id))
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
    """Return all sessions (full blobs with candles), pruning expired index entries."""
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


async def list_sessions_slim(limit: int = 50) -> list[dict]:
    """Return up to `limit` sessions using SLIM control blobs only (no candle arrays).
    Sorted newest-first.  Uses a Redis pipeline so all GETs are a single round trip.
    Use this for the sessions list API — loading candle arrays for 1000 sessions causes OOM."""
    from app.utils.redis_client import get_redis
    ids = await list_ids()
    if not ids:
        return []
    r = get_redis()
    # Fetch all control blobs in one pipeline round trip.
    pipe = r.pipeline()
    for sid in ids:
        pipe.get(_k(sid))
    raws = await pipe.execute()
    out: list[dict] = []
    stale: list[str] = []
    for sid, raw in zip(ids, raws):
        if raw is None:
            stale.append(sid)
        else:
            out.append(json.loads(raw))
    if stale:
        try:
            await r.srem(_INDEX, *stale)
        except Exception:
            pass
    out.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return out[:limit]


async def list_running_sessions() -> list[dict]:
    """Fast path for the background runner — loads SLIM control blobs only
    (no candle arrays).  Callers that need all_candles for replay advancement
    must call get_session() separately after this returns.

    Uses a separate Redis set (_RUNNING_IDX) maintained by save_session().
    Returns [] (not a full scan) when the running set is empty.
    """
    from app.utils.redis_client import get_redis
    try:
        r = get_redis()
        ids = list(await r.smembers(_RUNNING_IDX))
    except Exception:
        ids = []

    if not ids:
        return []

    out: list[dict] = []
    stale: list[str] = []
    for sid in ids:
        raw = await get_redis().get(_k(sid))   # slim blob only — no candle fetch
        if raw is None:
            stale.append(sid)
        else:
            s = json.loads(raw)
            if s.get("status") != "running":
                stale.append(sid)
            else:
                out.append(s)
    if stale:
        try:
            await get_redis().srem(_RUNNING_IDX, *stale)
        except Exception:
            pass
    return out
