"""Data Recordings — schedule which stocks to capture into the 1-second tick
dataset for an upcoming trading day, then browse/chart/backtest what was recorded.

This is a dedicated, paper-trading-independent capture path. A *recording* is just
a named set of symbols targeted at one clean trading day. The recording layer owns
the capture allowlist (`candle_capture:symbols`): the capture loop (session-runner)
writes 1-second ticks ONLY for symbols that belong to an active recording, so you
record exactly the stocks you selected — no more, no less.

Key guarantees:
  • Full-day only: a new recording always targets the *next* not-yet-opened session
    (today if it's a weekday before 09:15 IST, else the next weekday). You can never
    start a recording mid-day and get a gap at the open.
  • No stock limit: select 10, 50, 200 — the union is armed for capture.
  • The captured data is the same tick-store that backtests/replays read first, so a
    recording's day can be backtested per-symbol straight from the list.

Storage: Redis (persistent, no TTL). `recording:{id}` holds the JSON blob; the set
`recordings:index` holds all ids. Status is derived from the target date + current
IST time, so no background state machine is needed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.api.auth import get_current_user
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

IST = timezone(timedelta(hours=5, minutes=30))

_KEY_PREFIX  = "recording:"
_INDEX_KEY   = "recordings:index"
_ALLOWLIST   = "candle_capture:symbols"      # the capture loop reads this set
_OPEN_MIN    = 9 * 60 + 15                    # 09:15 IST
_CLOSE_MIN   = 15 * 60 + 30                   # 15:30 IST
_OPEN_GRACE  = 1                              # allow targeting today up to 09:16


# ── Time / target-day helpers ────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:            # skip Sat/Sun
        d += timedelta(days=1)
    return d


def _target_date(now: datetime | None = None) -> str:
    """The next clean trading day to record. Today only if it's a weekday and the
    open hasn't passed (so capture starts from 09:15 with no gap); otherwise the
    next weekday. Holidays just yield an empty recording — harmless."""
    now = now or _now_ist()
    now_min = now.hour * 60 + now.minute
    if now.weekday() < 5 and now_min <= (_OPEN_MIN + _OPEN_GRACE):
        return now.date().isoformat()
    return _next_weekday(now.date()).isoformat()


def _status(date_str: str, now: datetime | None = None) -> str:
    """Derive recording status from its target date and the current IST time."""
    now = now or _now_ist()
    today = now.date().isoformat()
    if date_str > today:
        return "scheduled"
    if date_str < today:
        return "completed"
    # target is today
    if now.weekday() >= 5:
        return "completed"
    now_min = now.hour * 60 + now.minute
    if now_min < _OPEN_MIN:
        return "scheduled"
    if now_min <= _CLOSE_MIN:
        return "recording"
    return "completed"


# ── Redis persistence ────────────────────────────────────────────────────────

async def _load(rec_id: str) -> Optional[dict]:
    from app.utils.redis_client import cache_get
    raw = await cache_get(_KEY_PREFIX + rec_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _load_all() -> list[dict]:
    from app.utils.redis_client import get_redis
    r = get_redis()
    ids = await r.smembers(_INDEX_KEY)
    ids = [i.decode() if isinstance(i, bytes) else str(i) for i in (ids or [])]
    if not ids:
        return []
    pipe = r.pipeline()
    for i in ids:
        pipe.get(_KEY_PREFIX + i)
    raws = await pipe.execute()
    out = []
    for raw in raws:
        if not raw:
            continue
        try:
            out.append(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
        except Exception:
            continue
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out


async def _save(rec: dict) -> None:
    from app.utils.redis_client import get_redis
    r = get_redis()
    await r.set(_KEY_PREFIX + rec["id"], json.dumps(rec))
    await r.sadd(_INDEX_KEY, rec["id"])


async def _delete(rec_id: str) -> None:
    from app.utils.redis_client import get_redis
    r = get_redis()
    await r.delete(_KEY_PREFIX + rec_id)
    await r.srem(_INDEX_KEY, rec_id)


# ── Capture allowlist sync ───────────────────────────────────────────────────

async def sync_capture_allowlist() -> list[str]:
    """Recompute `candle_capture:symbols` = union of symbols across every recording
    that is still scheduled or actively recording (target date >= today). Completed
    recordings drop out so we stop capturing yesterday's list. Also asks the feed
    service to stream those symbols (add-only — never unsubscribes symbols other
    features may depend on). Returns the armed symbol list."""
    from app.utils.redis_client import get_redis
    from app.utils import groww_feed

    now = _now_ist()
    recs = await _load_all()
    wanted: set[str] = set()
    for rec in recs:
        if _status(rec.get("date", ""), now) in ("scheduled", "recording"):
            for s in rec.get("symbols", []):
                wanted.add(str(s).upper())

    r = get_redis()
    current = await r.smembers(_ALLOWLIST)
    current = {c.decode() if isinstance(c, bytes) else str(c) for c in (current or [])}

    to_add = wanted - current
    to_rem = current - wanted
    if to_add:
        await r.sadd(_ALLOWLIST, *to_add)
        await groww_feed.request_symbols(list(to_add))    # ensure the feed streams them
    if to_rem:
        await r.srem(_ALLOWLIST, *to_rem)
    return sorted(wanted)


# ── Coverage ─────────────────────────────────────────────────────────────────

def _coverage_for(rec: dict) -> tuple[list[dict], dict]:
    """Per-symbol tick-store coverage for a recording's day + an aggregate summary."""
    from app.data.candle_store import day_coverage
    rows = [day_coverage(sym, rec["date"]) for sym in rec.get("symbols", [])]
    summary = {
        "symbols":        len(rows),
        "symbols_with_data": sum(1 for c in rows if c["ticks"] > 0),
        "full_day":       sum(1 for c in rows if c["full_day"]),
        "total_ticks":    sum(c["ticks"] for c in rows),
    }
    return rows, summary


def _view(rec: dict, now: datetime, with_coverage: bool = False) -> dict:
    out = {
        "id":         rec["id"],
        "name":       rec.get("name", ""),
        "date":       rec.get("date"),
        "symbols":    rec.get("symbols", []),
        "symbol_count": len(rec.get("symbols", [])),
        "note":       rec.get("note", ""),
        "status":     _status(rec.get("date", ""), now),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
    }
    if with_coverage:
        rows, summary = _coverage_for(rec)
        out["coverage"] = rows
        out["coverage_summary"] = summary
    else:
        # Cheap aggregate for the list view.
        _, summary = _coverage_for(rec)
        out["coverage_summary"] = summary
    return out


# ── Models ───────────────────────────────────────────────────────────────────

class CreateRecordingRequest(BaseModel):
    name:    str = Field(default="", max_length=120)
    symbols: list[str] = Field(default_factory=list)
    note:    str = Field(default="", max_length=500)


class UpdateRecordingRequest(BaseModel):
    name:    Optional[str] = None
    symbols: Optional[list[str]] = None
    note:    Optional[str] = None


class BacktestRecordingRequest(BaseModel):
    symbols: Optional[list[str]] = None   # subset; None/empty = all in the recording
    capital: float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    speed:   int = 10


def _clean_symbols(symbols: list[str]) -> list[str]:
    seen, out = set(), []
    for s in symbols or []:
        u = str(s).strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
async def list_recordings():
    now = _now_ist()
    recs = await _load_all()
    return {"status": "success", "data": [_view(r, now) for r in recs]}


@router.post("")
@router.post("/")
async def create_recording(req: CreateRecordingRequest, user: dict = Depends(get_current_user)):
    symbols = _clean_symbols(req.symbols)
    if not symbols:
        raise HTTPException(400, "Select at least one stock to record.")
    now = _now_ist()
    date = _target_date(now)
    rec = {
        "id":      uuid.uuid4().hex[:12],
        "name":    (req.name or "").strip() or f"Recording {date}",
        "symbols": symbols,
        "date":    date,
        "note":    (req.note or "").strip(),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await _save(rec)
    await sync_capture_allowlist()
    logger.info("Recording created",
                extra={"log_type": "recording_event", "event": "recording_create",
                       "recording_id": rec["id"], "date": date, "symbols": len(symbols)})
    return {"status": "success", "data": _view(rec, now)}


@router.get("/{rec_id}")
async def get_recording(rec_id: str):
    rec = await _load(rec_id)
    if not rec:
        raise HTTPException(404, "Recording not found.")
    return {"status": "success", "data": _view(rec, _now_ist(), with_coverage=True)}


@router.put("/{rec_id}")
async def update_recording(rec_id: str, req: UpdateRecordingRequest,
                           user: dict = Depends(get_current_user)):
    rec = await _load(rec_id)
    if not rec:
        raise HTTPException(404, "Recording not found.")
    now = _now_ist()
    if _status(rec.get("date", ""), now) == "completed":
        raise HTTPException(400, "Completed recordings can't be edited.")
    if req.name is not None:
        rec["name"] = req.name.strip() or rec["name"]
    if req.note is not None:
        rec["note"] = req.note.strip()
    if req.symbols is not None:
        symbols = _clean_symbols(req.symbols)
        if not symbols:
            raise HTTPException(400, "A recording needs at least one stock.")
        rec["symbols"] = symbols
    rec["updated_at"] = now.isoformat()
    await _save(rec)
    await sync_capture_allowlist()
    return {"status": "success", "data": _view(rec, now, with_coverage=True)}


@router.delete("/{rec_id}")
async def delete_recording(rec_id: str, user: dict = Depends(get_current_user)):
    rec = await _load(rec_id)
    if not rec:
        raise HTTPException(404, "Recording not found.")
    await _delete(rec_id)
    await sync_capture_allowlist()
    return {"status": "success"}


@router.get("/{rec_id}/chart/{symbol}")
async def recording_chart(rec_id: str, symbol: str, bar_seconds: int = 60):
    """Resampled OHLC bars for one recorded symbol/day, straight from the tick
    store — for rendering the recording chart. bar_seconds: 5/10/60/… """
    rec = await _load(rec_id)
    if not rec:
        raise HTTPException(404, "Recording not found.")
    symbol = symbol.upper()
    if symbol not in [s.upper() for s in rec.get("symbols", [])]:
        raise HTTPException(404, f"{symbol} is not part of this recording.")
    from app.data.candle_store import read_bars, day_coverage
    bar_seconds = max(1, min(int(bar_seconds), 3600))
    bars = read_bars(symbol, rec["date"], bar_seconds)
    cov  = day_coverage(symbol, rec["date"])
    return {"status": "success", "data": {
        "symbol": symbol, "date": rec["date"], "bar_seconds": bar_seconds,
        "candles": bars, "coverage": cov,
    }}


@router.post("/{rec_id}/backtest")
async def backtest_recording(rec_id: str, req: BacktestRecordingRequest,
                             user: dict = Depends(get_current_user)):
    """Launch intraday backtest/replay sessions for the recorded day. Each symbol
    runs as a server-side session that reads this recording's captured ticks first
    (via the tick-store). Returns the started session ids to watch in Live Sessions."""
    rec = await _load(rec_id)
    if not rec:
        raise HTTPException(404, "Recording not found.")
    if _status(rec.get("date", ""), _now_ist()) != "completed":
        raise HTTPException(400, "This recording's day isn't finished yet — nothing to backtest.")

    from app.api.sessions import start_session, StartSessionRequest
    from app.data.candle_store import day_coverage

    want = _clean_symbols(req.symbols) if req.symbols else [s.upper() for s in rec.get("symbols", [])]
    started, skipped = [], []
    for sym in want:
        if day_coverage(sym, rec["date"])["ticks"] <= 0:
            skipped.append({"symbol": sym, "reason": "no recorded ticks"})
            continue
        try:
            res = await start_session(StartSessionRequest(
                mode="backtest", symbol=sym, date=rec["date"],
                capital=req.capital, speed=req.speed if req.speed in (1, 2, 5, 10) else 10,
            ))
            started.append({"symbol": sym, "session_id": res["data"]["id"]})
        except HTTPException as exc:
            skipped.append({"symbol": sym, "reason": exc.detail})
        except Exception as exc:
            skipped.append({"symbol": sym, "reason": str(exc)[:120]})
    if not started:
        raise HTTPException(422, "No sessions could be started — none of the selected "
                                 "symbols have recorded data for this day.")
    logger.info("Recording backtest launched",
                extra={"log_type": "recording_event", "event": "recording_backtest",
                       "recording_id": rec_id, "started": len(started), "skipped": len(skipped)})
    return {"status": "success", "data": {"started": started, "skipped": skipped}}


# ── Maintenance loop ─────────────────────────────────────────────────────────

async def recordings_maintenance_loop() -> None:
    """Keep the capture allowlist correct across day rollovers: as a recording's day
    completes it should stop being captured, and a scheduled one's symbols should be
    armed ahead of its open. Cheap resync every few minutes; runs in the runner/full
    role only (same single-writer scope as the capture loop)."""
    import asyncio
    await asyncio.sleep(20)
    logger.info("recordings maintenance loop started",
                extra={"log_type": "app_lifecycle", "event": "recordings_maint_started"})
    while True:
        try:
            await sync_capture_allowlist()
        except Exception as exc:
            logger.debug("recordings maintenance resync failed: %s", exc)
        await asyncio.sleep(300)
