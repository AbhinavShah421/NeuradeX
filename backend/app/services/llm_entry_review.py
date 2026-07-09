"""LLM entry-dossier reviewer — SHADOW MODE.

At the moment the gate approves a long entry, the full dossier (panel votes,
levels, indicators, gate context) is handed to the local LLM for an
approve/veto verdict. The verdict is LOGGED AND PERSISTED but NEVER acted on:
entries execute exactly as before. After a couple of weeks the stored verdicts
can be joined against realised/CF outcomes — the LLM earns veto power only if
its vetoes measurably separate losers from winners, the same evidence bar
every other gate rule had to clear (see 2026-07-07/08 factor screens).

Runs as a fire-and-forget task: the trading loop never waits on it. A slow or
dead LLM costs nothing but a missing shadow row.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS llm_entry_reviews (
    id           SERIAL PRIMARY KEY,
    session_id   TEXT,
    symbol       TEXT NOT NULL,
    trade_date   DATE NOT NULL,
    candle_time  TEXT NOT NULL,
    price        DOUBLE PRECISION,
    verdict      TEXT,            -- approve | veto | parse_error | no_llm
    confidence   DOUBLE PRECISION,
    reason       TEXT,
    model        TEXT,
    latency_ms   INTEGER,
    dossier      JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, trade_date, candle_time)
);
"""
_ddl_ready = False

_SYSTEM = (
    "You are the final risk reviewer on an intraday long-only trading desk. "
    "You receive the entry dossier a rules-based system already approved. "
    "Judge whether this specific entry is sound. Reply with ONLY a JSON "
    'object: {"verdict": "approve" or "veto", "confidence": 0.0-1.0, '
    '"reason": "<one sentence>"}'
)


def _build_dossier(symbol: str, candle: dict, agents: list[dict], ind: dict,
                   gate_label: str, session: dict) -> dict:
    """Compact, LLM-readable snapshot of everything the gate saw."""
    votes = [{"agent": a.get("agent_name"), "vote": a.get("action"),
              "conf": round(float(a.get("confidence") or 0), 2)}
             for a in agents]
    ds = next((a for a in agents if a.get("agent_name") == "day_structure"), None)
    ind_ds = (ds or {}).get("indicators") or {}
    return {
        "symbol": symbol,
        "time": candle.get("time"),
        "price": candle.get("close"),
        "gate": gate_label,
        "mode": session.get("mode", "paper"),
        "votes": votes,
        "indicators": {k: ind.get(k) for k in
                       ("rsi", "vwap", "sma5", "sma20", "momentum_pct", "atr")},
        "levels": {"resistances": ind_ds.get("levels_res"),
                   "supports": ind_ds.get("levels_sup"),
                   "day_range_pct": ind_ds.get("day_range_pct"),
                   "rr_ratio": ind_ds.get("rr_ratio")},
    }


async def _ensure_table() -> None:
    global _ddl_ready
    if _ddl_ready:
        return
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        await conn.execute(text(_DDL))
    _ddl_ready = True


def _parse_verdict(reply: str) -> tuple[str, float | None, str]:
    try:
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        d = json.loads(m.group(0)) if m else {}
        v = str(d.get("verdict", "")).lower()
        if v not in ("approve", "veto"):
            return "parse_error", None, reply[:300]
        conf = d.get("confidence")
        conf = float(conf) if conf is not None else None
        return v, conf, str(d.get("reason", ""))[:300]
    except Exception:
        return "parse_error", None, (reply or "")[:300]


async def _review(dossier: dict, session_id: str | None) -> None:
    from app.utils.llm_client import llm_chat, active_model
    started = time.monotonic()
    # Prepend the distilled lessons from past losing trades (post-mortem loop,
    # ai_engine:active_lessons) so the reviewer judges each entry against the
    # system's own recorded failure modes — e.g. "chasing momentum into
    # resistance (12x)". Same cache the AIEngine manual analysis consults.
    lessons = ""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:active_lessons")
        if raw:
            lessons = f"{raw}\n\n"
    except Exception:
        pass
    prompt = lessons + "Entry dossier:\n" + json.dumps(dossier, default=str)
    # Generous budget: 8B on CPU needs ~40s model reload after idle plus
    # prefill+generation. Shadow reviews are async — latency costs nothing.
    reply = await llm_chat(prompt, _SYSTEM, temperature=0.1,
                           max_tokens=120, timeout=300.0)
    latency = int((time.monotonic() - started) * 1000)
    if reply is None:
        verdict, conf, reason = "no_llm", None, "LLM unavailable"
    else:
        verdict, conf, reason = _parse_verdict(reply)

    try:
        await _ensure_table()
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO llm_entry_reviews
                  (session_id, symbol, trade_date, candle_time, price, verdict,
                   confidence, reason, model, latency_ms, dossier)
                VALUES (:sid,:sym,:d,:ct,:px,:v,:c,:r,:m,:lat,:dos)
                ON CONFLICT (symbol, trade_date, candle_time) DO NOTHING
            """), {
                "sid": session_id, "sym": dossier["symbol"],
                "d": datetime.now().date(), "ct": dossier.get("time"),
                "px": dossier.get("price"), "v": verdict, "c": conf,
                "r": reason, "m": active_model(), "lat": latency,
                "dos": json.dumps(dossier, default=str),
            })
    except Exception as exc:
        logger.warning("llm entry review persist failed: %s", exc)

    logger.info("LLM shadow review: %s %s -> %s",
                dossier["symbol"], dossier.get("time"), verdict,
                extra={"log_type": "ai_engine", "event": "llm_entry_review",
                       "symbol": dossier["symbol"], "verdict": verdict,
                       "confidence": conf, "latency_ms": latency})


def shadow_review_entry(symbol: str, candle: dict, agents: list[dict],
                        ind: dict, gate_label: str, session: dict) -> None:
    """Fire-and-forget shadow review. Never blocks, never raises."""
    try:
        dossier = _build_dossier(symbol, candle, agents, ind, gate_label, session)
        asyncio.get_running_loop().create_task(
            _review(dossier, session.get("id")))
    except Exception as exc:
        logger.debug("shadow review not scheduled: %s", exc)
