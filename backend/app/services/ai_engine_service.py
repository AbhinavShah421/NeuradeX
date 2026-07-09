"""AI Engine business logic — analyze, record outcomes, performance, history.

This module holds the actual DB/data-provider access, calculations and
simulation logic that used to live inline inside the FastAPI route handlers
in app.api.ai_engine. app.api.ai_engine is now a thin router that parses
requests and delegates here.

A few names defined in this module are imported directly by other modules —
app.api.ai_engine re-exports them for backward compatibility, so do not
rename/remove them without also updating those call sites:
  - app.api.sessions: _log_system_event
  - app.api.delivery_paper: _ensure_scan_eval
"""
from __future__ import annotations
import asyncio
import os
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


# ── Request / Response models ────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol:   str
    candles:  list[dict]         # [{open,high,low,close,volume,timestamp}]
    context:  dict = {}
    capital:  float = 50_000.0
    position: str   = "NONE"    # "NONE" | "LONG"

class OutcomeRequest(BaseModel):
    prediction_id: str
    symbol:        str
    entry_price:   float
    exit_price:    float
    pnl:           float
    pnl_pct:       float


# ── Lazy init ─────────────────────────────────────────────────────────────────

async def _ensure_db() -> None:
    from app.agents import get_learning, get_memory
    await get_learning().init_db()
    await get_memory().init_db()

_db_ready = False

async def _db_once() -> None:
    global _db_ready
    if not _db_ready:
        await _ensure_db()
        _db_ready = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

async def analyze(req: AnalyzeRequest):
    """Run all 6 agents and return the ensemble decision."""
    await _db_once()
    from app.agents import get_engine, get_learning, get_rl_agent

    engine   = get_engine()
    learning = get_learning()

    # Apply learned weights
    weights = await learning.get_weights()
    if weights:
        engine.update_weights(weights)

    context = {
        **req.context,
        "symbol":   req.symbol.upper(),
        "capital":  req.capital,
        "position": req.position,
    }

    decision = await engine.decide(req.symbol.upper(), req.candles, context)

    # RL state for learning
    rl_state: Optional[int] = None
    try:
        rl_state = get_rl_agent().extract_state(req.candles)
    except Exception:
        logger.warning("RL state extraction failed for %s; continuing without RL state", req.symbol, exc_info=True)

    # Fingerprint for pattern-memory learning
    fingerprint = None
    try:
        from app.agents.fingerprint import build_fingerprint
        fingerprint = build_fingerprint(req.candles)
    except Exception:
        logger.warning("Fingerprint build failed for %s; continuing without fingerprint", req.symbol, exc_info=True)

    # Persist prediction (with fingerprint so the outcome can feed memory later)
    await learning.store_prediction(decision, _candle_time(req.candles), context, rl_state, fingerprint)

    return {
        "prediction_id":   decision.prediction_id,
        "action":          decision.action,
        "confidence":      decision.confidence,
        "agent_agreement": decision.agent_agreement,
        "risk_score":      decision.risk_score,
        "reasoning":       decision.reasoning,
        "timestamp":       decision.timestamp.isoformat() if decision.timestamp else None,
        "agents": [
            {
                "agent_name": s.agent_name,
                "action":     s.action,
                "confidence": s.confidence,
                "weight":     round(s.weight, 3),
                "reasoning":  s.reasoning,
                "indicators": s.indicators,
            }
            for s in decision.agents
        ],
        "rl_state": rl_state,
    }


async def record_outcome(req: OutcomeRequest):
    """Record trade outcome — record_outcome updates agent weights + RL Q-table + memory."""
    await _db_once()
    from app.agents import get_learning

    reward = await get_learning().record_outcome(
        req.prediction_id, req.symbol,
        req.entry_price, req.exit_price,
        req.pnl, req.pnl_pct,
    )
    return {"reward": round(reward, 4), "status": "recorded"}


async def get_performance():
    """Per-agent weight and accuracy stats."""
    await _db_once()
    from app.agents import get_learning
    return await get_learning().get_performance()


async def get_agent_action_trend(agent: str, action: str):
    """Per-day accuracy trend for a specific agent + action (BUY/SELL/HOLD).

    Returns a list of {date, total, correct, accuracy} sorted by date so the
    frontend can plot a line chart showing how this agent's vote quality has
    changed over time.
    """
    await _db_once()
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT
                    DATE(p.created_at AT TIME ZONE 'UTC')            AS date,
                    COUNT(*)::int                                     AS total,
                    SUM(CASE WHEN o.outcome='correct' THEN 1 ELSE 0 END)::int AS correct
                FROM ai_engine_predictions p
                JOIN ai_engine_outcomes o USING (prediction_id)
                CROSS JOIN LATERAL jsonb_array_elements(p.agent_signals::jsonb) AS sig
                WHERE sig->>'agent' = :agent
                  AND sig->>'action' = :action
                GROUP BY DATE(p.created_at AT TIME ZONE 'UTC')
                ORDER BY date ASC
            """), {"agent": agent.lower(), "action": action.upper()})).fetchall()

        points = []
        for r in rows:
            total   = r[1] or 0
            correct = r[2] or 0
            points.append({
                "date":     str(r[0]),
                "total":    total,
                "correct":  correct,
                "accuracy": round(correct / total, 3) if total > 0 else 0.0,
            })
        return {"status": "success", "data": {"agent": agent, "action": action.upper(), "points": points}}
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "data": {"agent": agent, "action": action.upper(), "points": []}}


async def get_history(symbol: Optional[str] = None, limit: int = 20):
    """Recent predictions with outcomes."""
    await _db_once()
    from app.agents import get_learning
    rows = await get_learning().get_recent_predictions(symbol, limit)
    # Convert datetime objects to strings for JSON
    for r in rows:
        if hasattr(r.get("created_at"), "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    return rows


async def get_weights():
    """Current agent weights."""
    await _db_once()
    from app.agents import get_learning
    return await get_learning().get_weights()


async def learning_summary():
    """Aggregate training status — proves every backtest/paper trade trains the agents."""
    await _db_once()
    from app.agents import get_learning, get_memory
    from app.database.postgres import engine
    from sqlalchemy import text

    perf = await get_learning().get_performance()
    mem  = await get_memory().stats()

    totals = {"predictions": 0, "outcomes": 0, "recent_outcomes_24h": 0}
    overall_acc = 0.0
    try:
        async with engine.begin() as conn:
            totals["predictions"] = int((await conn.execute(
                text("SELECT COUNT(*) FROM ai_engine_predictions"))).scalar() or 0)
            totals["outcomes"] = int((await conn.execute(
                text("SELECT COUNT(*) FROM ai_engine_outcomes"))).scalar() or 0)
            totals["recent_outcomes_24h"] = int((await conn.execute(
                text("SELECT COUNT(*) FROM ai_engine_outcomes WHERE created_at > NOW() - INTERVAL '24 hours'"))).scalar() or 0)
            row = (await conn.execute(text(
                "SELECT AVG(CASE WHEN outcome='correct' THEN 1.0 ELSE 0.0 END) FROM ai_engine_outcomes"))).fetchone()
            overall_acc = float(row[0]) if row and row[0] is not None else 0.0
    except Exception as exc:
        logger.warning("learning_summary totals failed: %s", exc)

    return {
        "status": "success",
        "data": {
            "totals": totals,
            "overall_accuracy": round(overall_acc, 3),
            "agents": perf,
            "memory_cases": mem.get("total_cases", 0),
            "memory_by_source": mem.get("by_source", []),
        },
    }


# ── AI Watchlist (self-running market scanner) ────────────────────────────────

async def get_watchlist(min_grade: str | None = None):
    """The live AI watchlist produced by the stock-scanner service (read from
    Redis), each item enriched with its latest LLM news-sentiment signal
    (produced by the sentiment-service).

    min_grade: optional A/B/C filter — keep only items at or above that grade
    (A is best). Use it to surface only high win-probability setups."""
    import json
    from app.utils.redis_client import cache_get
    _grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    try:
        raw = await cache_get("ai_engine:watchlist")
        if raw:
            data = json.loads(raw)
            if min_grade and min_grade.upper() in _grade_rank:
                cutoff = _grade_rank[min_grade.upper()]
                kept = [it for it in data.get("items", [])
                        if _grade_rank.get((it.get("grade") or "D").upper(), 3) <= cutoff]
                data["items"] = kept
                data["filtered_min_grade"] = min_grade.upper()
            # Enrich every surfaced list (intraday items + delivery) with the
            # latest LLM news-sentiment signal for each symbol.
            for item in [*data.get("items", []), *data.get("delivery", [])]:
                sym = (item.get("symbol") or "").upper()
                if not sym:
                    continue
                try:
                    s = await cache_get(f"ai_engine:sentiment:{sym}")
                    if s:
                        sd = json.loads(s)
                        if int(sd.get("headlines_count", 0)) > 0:
                            item["news"] = {
                                "sentiment": sd.get("sentiment"),
                                "score": sd.get("score"),
                                "action": sd.get("action"),
                                "confidence": sd.get("confidence"),
                                "catalyst": sd.get("catalyst"),
                                "summary": sd.get("summary"),
                                "headlines_count": sd.get("headlines_count"),
                                "top_headlines": sd.get("top_headlines", []),
                                "updated_at": sd.get("updated_at"),
                            }
                except Exception:
                    logger.debug("sentiment enrichment failed for %s in watchlist", sym, exc_info=True)
            return {"status": "success", "data": data}
    except Exception as exc:
        logger.debug("watchlist read failed: %s", exc)
    return {"status": "success", "data": {"updated_at": None, "scanned": 0, "universe": 0, "items": []}}


async def get_ranked(limit: int = 100):
    """The full ranked board of AI-scanned stocks (for the Predictions page),
    each enriched with its latest LLM news-sentiment. `limit` caps how many of the
    top-ranked names to return."""
    import json
    from app.utils.redis_client import cache_get
    try:
        raw = await cache_get("ai_engine:ranked")
        if raw:
            data = json.loads(raw)
            items = (data.get("items") or [])[: max(1, min(limit, 250))]
            for item in items:
                sym = (item.get("symbol") or "").upper()
                if not sym:
                    continue
                try:
                    s = await cache_get(f"ai_engine:sentiment:{sym}")
                    if s:
                        sd = json.loads(s)
                        if int(sd.get("headlines_count", 0)) > 0:
                            item["news"] = {
                                "sentiment": sd.get("sentiment"), "score": sd.get("score"),
                                "action": sd.get("action"), "confidence": sd.get("confidence"),
                                "catalyst": sd.get("catalyst"), "summary": sd.get("summary"),
                                "headlines_count": sd.get("headlines_count"),
                                "top_headlines": sd.get("top_headlines", []),
                                "updated_at": sd.get("updated_at"),
                            }
                except Exception:
                    logger.debug("sentiment enrichment failed for %s in ranked board", sym, exc_info=True)
            return {"status": "success", "data": {**data, "items": items, "returned": len(items)}}
    except Exception as exc:
        logger.debug("ranked board read failed: %s", exc)
    return {"status": "success", "data": {"updated_at": None, "scanned": 0, "universe": 0, "items": [], "returned": 0}}


def _rank_change_reason(cur: dict, prev: dict) -> str:
    """Explain *why* a stock's rank moved between two scans, from the components
    the scanner actually ranks on (action, grade, win-prob, signal/rank score, news)."""
    bits: list[str] = []
    ca, pa = cur.get("action"), prev.get("action")
    if ca != pa:
        bits.append(f"call {pa}→{ca}")
    cg, pg = cur.get("grade"), prev.get("grade")
    if cg != pg:
        bits.append(f"grade {pg}→{cg}")
    cw, pw = cur.get("win_probability"), prev.get("win_probability")
    if cw is not None and pw is not None and abs(cw - pw) >= 0.02:
        bits.append(f"win-prob {pw*100:.0f}%→{cw*100:.0f}%")
    cs, ps = cur.get("rank_score", cur.get("signal_score")), prev.get("rank_score", prev.get("signal_score"))
    if cs is not None and ps is not None and abs(cs - ps) >= 0.5:
        bits.append(f"score {ps:.0f}→{cs:.0f}")
    cn = (cur.get("news") or {}).get("catalyst")
    if cn and not (prev.get("news") or {}).get("catalyst"):
        bits.append("fresh news catalyst")
    return ", ".join(bits) if bits else "minor re-ordering vs other names"


async def scan_diff(limit: int = 60):
    """How this scan's ranking differs from the previous completed scan: per-stock
    rank moves (with the reason), names that entered the board, and names that
    dropped out. Powers the AI Watchlist 'what changed' view."""
    import json
    from app.utils.redis_client import cache_get
    cur_items: list[dict] = []
    prev_items: list[dict] = []
    cur_meta: dict = {}
    prev_meta: dict = {}
    try:
        raw = await cache_get("ai_engine:ranked")
        if raw:
            d = json.loads(raw); cur_items = d.get("items") or []
            cur_meta = {"updated_at": d.get("updated_at"), "candidates": d.get("candidates")}
        rawp = await cache_get("ai_engine:ranked:prev")
        if rawp:
            d = json.loads(rawp); prev_items = d.get("items") or []
            prev_meta = {"updated_at": d.get("updated_at"), "candidates": d.get("candidates")}
    except Exception as exc:
        logger.debug("scan-diff read failed: %s", exc)

    if not prev_items:
        return {"status": "success", "data": {
            "available": False, "current": cur_meta, "previous": prev_meta,
            "moved": [], "entered": [], "dropped": [],
            "message": "No previous scan to compare yet — diff appears after the next rescan.",
        }}

    cur_by = {(i.get("symbol") or "").upper(): i for i in cur_items}
    prev_rank = {(i.get("symbol") or "").upper(): i.get("rank") for i in prev_items}
    prev_by = {(i.get("symbol") or "").upper(): i for i in prev_items}

    moved: list[dict] = []
    entered: list[dict] = []
    for sym, c in cur_by.items():
        cr = c.get("rank")
        if sym in prev_rank:
            pr = prev_rank[sym]
            if cr != pr:
                moved.append({
                    "symbol": sym, "name": c.get("name"), "rank": cr, "prev_rank": pr,
                    "delta": pr - cr,                      # +ve = climbed (lower rank number)
                    "direction": "up" if cr < pr else "down",
                    "grade": c.get("grade"), "action": c.get("action"),
                    "reason": _rank_change_reason(c, prev_by[sym]),
                })
        else:
            entered.append({
                "symbol": sym, "name": c.get("name"), "rank": cr,
                "grade": c.get("grade"), "action": c.get("action"),
                "reason": _rank_change_reason(c, {}),
            })
    dropped = [{
        "symbol": sym, "name": p.get("name"), "prev_rank": p.get("rank"),
        "grade": p.get("grade"), "action": p.get("action"),
    } for sym, p in prev_by.items() if sym not in cur_by]

    moved.sort(key=lambda m: -abs(m["delta"]))
    entered.sort(key=lambda e: e["rank"] or 999)
    dropped.sort(key=lambda d: d["prev_rank"] or 999)
    return {"status": "success", "data": {
        "available": True, "current": cur_meta, "previous": prev_meta,
        "moved": moved[:limit], "entered": entered[:limit], "dropped": dropped[:limit],
        "counts": {"moved": len(moved), "entered": len(entered), "dropped": len(dropped)},
    }}


async def scan_watchlist():
    """Ask the stock-scanner microservice to run an immediate full market sweep."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{settings.SCANNER_SERVICE_URL}/scan")
        return {"status": "started"}
    except Exception as exc:
        logger.warning("could not trigger scanner: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def backfill_delivery(days: int = 14, limit: int = 250):
    """Ask the scanner to reconstruct delivery-pick accuracy history so the AI Scan
    Accuracy graph shows a delivery line immediately (live grading continues daily)."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{settings.SCANNER_SERVICE_URL}/backfill-delivery",
                              params={"days": days, "limit": limit})
        return {"status": "started", "days": days, "limit": limit}
    except Exception as exc:
        logger.warning("could not trigger delivery backfill: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def backfill_committed(days: int = 20, limit: int = 400):
    """Ask the scanner to reconstruct the high-conviction tier's accuracy history."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{settings.SCANNER_SERVICE_URL}/backfill-committed",
                              params={"days": days, "limit": limit})
        return {"status": "started", "days": days, "limit": limit}
    except Exception as exc:
        logger.warning("could not trigger committed backfill: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def get_auto_scan():
    """Return whether the continuous background scan loop is enabled."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.SCANNER_SERVICE_URL}/auto-scan")
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        logger.debug("auto-scan status proxy failed: %s", exc)
    return {"status": "success", "data": {"enabled": True}}


async def set_auto_scan(enabled: bool):
    """Enable or disable the continuous background scan loop."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{settings.SCANNER_SERVICE_URL}/auto-scan",
                params={"enabled": str(enabled).lower()},
            )
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        logger.warning("auto-scan toggle proxy failed: %s", exc)
    return {"status": "error", "detail": "scanner unavailable"}


async def regime_detail():
    """Full market-regime breakdown: raw indicators, conditions, and methodology."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.SCANNER_SERVICE_URL}/regime-detail")
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        logger.debug("regime-detail proxy failed: %s", exc)
    return {"status": "success", "data": {}}


async def scan_status():
    """Centralized scan status (shared by Dashboard / Predictions / Portfolio):
    whether a sweep is running, progress, and when it last completed. Single source
    of truth so a rescan started on one page disables rescan everywhere."""
    import httpx, json
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            status_r, auto_r = await asyncio.gather(
                client.get(f"{settings.SCANNER_SERVICE_URL}/status"),
                client.get(f"{settings.SCANNER_SERVICE_URL}/auto-scan"),
                return_exceptions=True,
            )
            if not isinstance(status_r, Exception) and status_r.status_code == 200:
                d = (status_r.json() or {}).get("data", {})
                auto_enabled = True
                if not isinstance(auto_r, Exception) and auto_r.status_code == 200:
                    auto_enabled = bool((auto_r.json() or {}).get("data", {}).get("enabled", True))
                return {"status": "success", "data": {
                    "scanning": bool(d.get("scanning")),
                    "running": bool(d.get("running")),
                    "scanned": d.get("scanned", 0),
                    "universe": d.get("universe", 0),
                    "candidates": d.get("candidates", 0),
                    "last_scan": d.get("last_scan"),
                    "market_regime": d.get("market_regime"),
                    "auto_scan_enabled": auto_enabled,
                }}
    except Exception as exc:
        logger.debug("scan-status proxy failed: %s", exc)
    # Fallback: read the watchlist payload's scanning flag from Redis.
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:watchlist")
        if raw:
            data = json.loads(raw)
            return {"status": "success", "data": {
                "scanning": bool(data.get("scanning")),
                "scanned": data.get("scanned", 0), "universe": data.get("universe", 0),
                "candidates": data.get("candidates", 0), "last_scan": data.get("updated_at"),
                "market_regime": data.get("market_regime"),
            }}
    except Exception:
        logger.debug("scan-status redis fallback failed", exc_info=True)
    return {"status": "success", "data": {"scanning": False, "scanned": 0, "universe": 0}}


# ── AI Loss Post-Mortem & Lessons ─────────────────────────────────────────────
# Owns the "why did this trade lose, and what do we learn" loop:
#   1. Pull losing closed trades (with their recorded agent signals + market
#      context) from the feedback-service.
#   2. Have the LLM explain the root cause + a reusable failure_mode + the lesson.
#   3. Persist each post-mortem; aggregate recurring failure modes into "lessons".
#   4. Cache the active lessons so the AI's decision prompts can consult them next
#      time (complements the quantitative pattern-memory veto already in the
#      ensemble).

_ACTIVE_LESSONS_KEY = "ai_engine:active_lessons"

_POSTMORTEM_DDL = """
CREATE TABLE IF NOT EXISTS trade_postmortems (
    id            SERIAL PRIMARY KEY,
    trade_key     TEXT UNIQUE,
    symbol        TEXT,
    action        TEXT,
    source        TEXT,
    pnl_pct       DOUBLE PRECISION,
    root_cause    TEXT,
    failure_mode  TEXT,
    factors       JSONB,
    lesson        TEXT,
    avoid_when    TEXT,
    confidence    DOUBLE PRECISION,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
"""


def _trade_key(t: dict) -> str:
    return f"{(t.get('symbol') or '').upper()}|{t.get('timestamp_open') or t.get('trade_id') or ''}"


def _extract_json(txt) -> dict | None:
    if not txt:
        return None
    import json as _json
    s = txt.strip()
    if "```" in s:
        parts = s.split("```")
        s = parts[1] if len(parts) >= 2 else s
        if s.lower().startswith("json"):
            s = s[4:]
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        return _json.loads(s[a:b + 1])
    except Exception:
        logger.debug("Failed to parse LLM JSON response", exc_info=True)
        return None


def _rule_postmortem(t: dict) -> dict:
    """Deterministic fallback explanation when the LLM is unavailable."""
    mc = t.get("market_context") or {}
    rsi = mc.get("rsi")
    regime = mc.get("market_regime") or mc.get("regime")
    mom = mc.get("momentum_pct") if mc.get("momentum_pct") is not None else mc.get("mom5")
    factors, fm = [], "Price reversed after entry"
    act = (t.get("action") or "").upper()
    if act == "BUY":
        if isinstance(rsi, (int, float)) and rsi > 70:
            fm = "Bought overbought (RSI > 70)"; factors.append(f"RSI was {rsi} at entry — little upside left")
        if regime == "bearish":
            fm = "Bought against a bearish market"; factors.append("Broad market regime was bearish")
        if isinstance(mom, (int, float)) and mom < 0:
            factors.append(f"Momentum was negative ({mom}%) at entry")
    if not factors:
        factors = ["The move went against the position after entry"]
    return {
        "root_cause": f"{t.get('symbol')} moved against the {act} after entry (P&L {t.get('pnl_pct')}%).",
        "failure_mode": fm,
        "factors": factors,
        "lesson": "Re-check RSI, momentum and market-regime alignment before taking a similar setup.",
        "avoid_when": fm,
        "confidence": 0.4,
    }


async def _llm_postmortem(t: dict) -> dict | None:
    import json as _json
    from app.utils.llm_client import llm_chat
    mc = _json.dumps(t.get("market_context") or {})[:700]
    ag = _json.dumps(t.get("agent_signals") or {})[:700]
    prompt = f"""A real trade LOST money. Using only the evidence, explain exactly WHY it lost and the lesson to learn.

Trade: {t.get('symbol')} · {t.get('action')} · entry {t.get('entry_price')} → exit {t.get('exit_price')} · P&L {t.get('pnl_pct')}% · held {t.get('duration_minutes')}m · source {t.get('trade_source')}
Market context at entry: {mc}
Agent signals at entry: {ag}

Respond with ONLY valid JSON:
{{"root_cause": str, "failure_mode": "a short reusable category (e.g. 'chased momentum into resistance', 'ignored bearish regime', 'stop too tight')", "factors": [str, ...], "lesson": str, "avoid_when": "the condition to avoid next time", "confidence": number 0..1}}"""
    try:
        txt = await llm_chat(prompt, system="You are a precise trading risk analyst. Output only valid JSON.",
                             temperature=0.2, max_tokens=550, timeout=30.0)
    except Exception:
        logger.debug("LLM postmortem call failed for %s; falling back to rule-based", t.get("symbol"), exc_info=True)
        txt = None
    parsed = _extract_json(txt)
    return parsed if parsed and parsed.get("root_cause") else None


async def _refresh_active_lessons() -> list[dict]:
    """Aggregate stored post-mortems into ranked lessons and cache a compact text
    version for the decision prompts."""
    from sqlalchemy import text
    from app.database.postgres import engine
    lessons: list[dict] = []
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_POSTMORTEM_DDL))
            rows = (await conn.execute(text("""
                SELECT failure_mode, COUNT(*) AS n, AVG(pnl_pct) AS avg_loss,
                       MAX(lesson) AS lesson, MAX(avoid_when) AS avoid_when
                FROM trade_postmortems
                WHERE failure_mode IS NOT NULL AND failure_mode <> ''
                GROUP BY failure_mode ORDER BY n DESC, avg_loss ASC LIMIT 12
            """))).fetchall()
        for r in rows:
            lessons.append({
                "failure_mode": r[0], "occurrences": int(r[1]),
                "avg_loss_pct": round(float(r[2] or 0), 2),
                "lesson": r[3], "avoid_when": r[4],
            })
    except Exception as exc:
        logger.warning("lessons aggregation failed: %s", exc)
    try:
        from app.utils.redis_client import cache_set
        if lessons:
            txt = "LESSONS FROM PAST LOSING TRADES (avoid repeating these):\n" + "\n".join(
                f"- {l['failure_mode']} ({l['occurrences']}× · avg {l['avg_loss_pct']}%): {l.get('avoid_when') or l.get('lesson') or ''}"
                for l in lessons[:8])
            await cache_set(_ACTIVE_LESSONS_KEY, txt, expire=86400 * 14)
    except Exception:
        logger.debug("Failed to persist active lessons cache", exc_info=True)
    return lessons


async def loss_learning_run(limit: int = 60, max_new: int = 15):
    """Analyse recent losing trades that don't yet have a post-mortem, store the
    AI explanations, and refresh the aggregated lessons."""
    import httpx, json as _json
    from sqlalchemy import text
    from app.database.postgres import engine
    from app.config import settings

    # 1. Pull losing trades from the feedback-service.
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{settings.FEEDBACK_SERVICE_URL}/trades", params={"limit": limit})
            trades = r.json() if r.status_code == 200 else []
    except Exception as exc:
        logger.warning("loss-learning: could not read feedback trades: %s", exc)
        trades = []
    losses = [t for t in trades
              if t.get("outcome") == "LOSS" or (t.get("pnl_pct") is not None and float(t.get("pnl_pct")) < 0)]

    analyzed = 0
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_POSTMORTEM_DDL))
            existing = {row[0] for row in (await conn.execute(text("SELECT trade_key FROM trade_postmortems"))).fetchall()}
            for t in losses:
                if analyzed >= max_new:
                    break
                key = _trade_key(t)
                if key in existing:
                    continue
                pm = await _llm_postmortem(t) or _rule_postmortem(t)
                await conn.execute(text("""
                    INSERT INTO trade_postmortems
                      (trade_key, symbol, action, source, pnl_pct, root_cause, failure_mode, factors, lesson, avoid_when, confidence)
                    VALUES (:k,:sym,:act,:src,:pnl,:rc,:fm,:fac,:les,:aw,:conf)
                    ON CONFLICT (trade_key) DO NOTHING
                """), {
                    "k": key, "sym": t.get("symbol"), "act": t.get("action"), "src": t.get("trade_source"),
                    "pnl": t.get("pnl_pct"), "rc": pm.get("root_cause"), "fm": pm.get("failure_mode"),
                    "fac": _json.dumps(pm.get("factors") or []), "les": pm.get("lesson"),
                    "aw": pm.get("avoid_when"), "conf": pm.get("confidence"),
                })
                analyzed += 1
    except Exception as exc:
        logger.warning("loss-learning persist failed: %s", exc)

    lessons = await _refresh_active_lessons()
    return {"status": "success", "data": {"losing_trades": len(losses), "newly_analyzed": analyzed, "lessons": len(lessons)}}


async def loss_learning_postmortems(limit: int = 50):
    """Recent loss post-mortems (why each losing trade lost)."""
    import json as _json
    from sqlalchemy import text
    from app.database.postgres import engine
    items = []
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_POSTMORTEM_DDL))
            rows = (await conn.execute(text("""
                SELECT symbol, action, source, pnl_pct, root_cause, failure_mode, factors, lesson, avoid_when, confidence, created_at
                FROM trade_postmortems ORDER BY created_at DESC LIMIT :lim
            """), {"lim": max(1, min(limit, 200))})).fetchall()
        for r in rows:
            items.append({
                "symbol": r[0], "action": r[1], "source": r[2], "pnl_pct": r[3],
                "root_cause": r[4], "failure_mode": r[5],
                "factors": (_json.loads(r[6]) if isinstance(r[6], str) else r[6]) or [],
                "lesson": r[7], "avoid_when": r[8], "confidence": r[9],
                "created_at": r[10].isoformat() if r[10] else None,
            })
    except Exception as exc:
        logger.warning("postmortems read failed: %s", exc)
    return {"status": "success", "data": {"items": items, "count": len(items)}}


async def loss_learning_lessons():
    """The aggregated lessons the AI has learned from losing trades."""
    return {"status": "success", "data": {"lessons": await _refresh_active_lessons()}}


# ── Scanner post-market signal score (learning feedback) ──────────────────────

class ScanFeedback(BaseModel):
    date:                    str
    evaluated_at:            Optional[str] = None
    picks:                   int = 0
    hits:                    int = 0
    accuracy:                float = 0.0
    avg_realized_return_pct: float = 0.0
    by_action:               dict = {}
    results:                 list[dict] = []
    trade_kind:              str = "intraday"   # "intraday" | "delivery"


# Accuracy goal. The broad scan realistically sits ~50% (markets are near-random
# at the single-pick level); the high-conviction "committed" tier is what we tune
# toward this target via selectivity + abstention.
SCAN_ACCURACY_TARGET = float(os.getenv("SCAN_ACCURACY_TARGET", "0.90"))

_SCAN_EVAL_DDL = """
CREATE TABLE IF NOT EXISTS scan_evaluations (
    id                     SERIAL PRIMARY KEY,
    eval_date              DATE NOT NULL,
    symbol                 TEXT NOT NULL,
    action                 TEXT,
    predicted_confidence   DOUBLE PRECISION,
    predicted_signal_score DOUBLE PRECISION,
    day_return_pct         DOUBLE PRECISION,
    realized_return_pct    DOUBLE PRECISION,
    correct                BOOLEAN,
    trade_kind             TEXT DEFAULT 'intraday',
    created_at             TIMESTAMPTZ DEFAULT NOW()
);
"""
# Idempotent migrations (old installs had UNIQUE(eval_date,symbol) and no trade_kind).
_SCAN_EVAL_MIGRATE = [
    "ALTER TABLE scan_evaluations ADD COLUMN IF NOT EXISTS trade_kind TEXT DEFAULT 'intraday'",
    "ALTER TABLE scan_evaluations DROP CONSTRAINT IF EXISTS scan_evaluations_eval_date_symbol_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS scan_evaluations_uniq ON scan_evaluations (eval_date, symbol, trade_kind)",
    # Per-factor snapshot at scan time — enables fitting the factor weights to
    # outcomes (the blended score alone was flat 48-53% across its range).
    "ALTER TABLE scan_evaluations ADD COLUMN IF NOT EXISTS factors JSONB",
]
_scan_eval_ready = False


async def _ensure_scan_eval() -> None:
    """Create + migrate the scan_evaluations table once (each migration in its own
    transaction so one no-op failure can't abort the rest)."""
    global _scan_eval_ready
    if _scan_eval_ready:
        return
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        await conn.execute(text(_SCAN_EVAL_DDL))
    for stmt in _SCAN_EVAL_MIGRATE:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as exc:
            logger.debug("scan_eval migrate skipped (%s): %s", stmt[:40], exc)
    _scan_eval_ready = True


async def scan_feedback(req: ScanFeedback):
    """Receive the scanner's post-market grade and persist each pick's outcome so
    it feeds the system's learning record (and the signal-score history)."""
    import json as _json
    from datetime import date
    from sqlalchemy import text
    from app.database.postgres import engine
    try:
        eval_date = date.fromisoformat(req.date)
    except (ValueError, TypeError):
        eval_date = date.today()
    inserted = 0
    try:
        await _ensure_scan_eval()
        async with engine.begin() as conn:
            for g in req.results:
                kind = g.get("trade_kind") or req.trade_kind or "intraday"
                await conn.execute(text("""
                    INSERT INTO scan_evaluations
                      (eval_date, symbol, action, predicted_confidence,
                       predicted_signal_score, day_return_pct, realized_return_pct,
                       correct, trade_kind, factors)
                    VALUES (:d,:sym,:act,:pc,:ps,:dr,:rr,:ok,:kind,:fac)
                    ON CONFLICT (eval_date, symbol, trade_kind) DO UPDATE SET
                      action=:act, predicted_confidence=:pc, predicted_signal_score=:ps,
                      day_return_pct=:dr, realized_return_pct=:rr, correct=:ok,
                      factors=COALESCE(EXCLUDED.factors, scan_evaluations.factors)
                """), {
                    "d": eval_date, "sym": g.get("symbol"), "act": g.get("action"),
                    "pc": g.get("predicted_confidence"), "ps": g.get("predicted_signal_score"),
                    "dr": g.get("day_return_pct"), "rr": g.get("realized_return_pct"),
                    "ok": bool(g.get("correct")), "kind": kind,
                    "fac": _json.dumps(g.get("factors")) if g.get("factors") else None,
                })
                inserted += 1
    except Exception as exc:
        logger.warning("scan_feedback persist failed: %s", exc)
    logger.info("scan feedback %s: %d picks, accuracy %.0f%%",
                req.date, req.picks, (req.accuracy or 0) * 100)
    return {"status": "recorded", "stored": inserted, "date": req.date}


async def scan_evaluation():
    """Latest post-market signal-score grade + the accuracy trend over time.

    The detailed latest grade comes straight from the scanner (Redis); the trend
    is the per-day accuracy history persisted from each feedback push."""
    import json
    from app.utils.redis_client import cache_get
    from sqlalchemy import text
    from app.database.postgres import engine

    latest = None
    try:
        raw = await cache_get("ai_engine:scan_eval:latest")
        if raw:
            latest = json.loads(raw)
    except Exception as exc:
        logger.debug("scan eval read failed: %s", exc)

    target = SCAN_ACCURACY_TARGET
    trends: dict[str, list[dict]] = {"intraday": [], "delivery": [], "committed": []}
    overalls: dict[str, dict] = {k: {"days": 0, "accuracy": None, "picks": 0} for k in trends}
    try:
        await _ensure_scan_eval()
        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT eval_date, COALESCE(trade_kind,'intraday') kind,
                       AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) AS acc,
                       AVG(realized_return_pct) AS avg_ret,
                       COUNT(*) AS n
                FROM scan_evaluations
                GROUP BY eval_date, COALESCE(trade_kind,'intraday')
                ORDER BY eval_date ASC
            """))).fetchall()
            for r in rows:
                kind = r[1] if r[1] in trends else "intraday"
                acc = round(float(r[2]), 4)
                trends[kind].append({
                    "date": r[0].strftime("%Y-%m-%d") if r[0] else None,
                    "accuracy": acc,
                    "avg_realized_return_pct": round(float(r[3] or 0.0), 2),
                    "picks": int(r[4]),
                    "meets_target": acc >= target,
                })
            agg = (await conn.execute(text("""
                SELECT COALESCE(trade_kind,'intraday') kind,
                       AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END), COUNT(*),
                       AVG(realized_return_pct)
                FROM scan_evaluations GROUP BY COALESCE(trade_kind,'intraday')
            """))).fetchall()
            for a in agg:
                kind = a[0] if a[0] in trends else "intraday"
                overalls[kind] = {"days": len(trends[kind]), "accuracy": round(float(a[1]), 4),
                                  "picks": int(a[2]), "meets_target": float(a[1]) >= target,
                                  "avg_return": round(float(a[3] or 0.0), 2)}
    except Exception as exc:
        logger.debug("scan eval trend failed: %s", exc)

    return {"status": "success", "data": {
        "latest": latest, "target": target,
        "trend": trends["intraday"], "delivery_trend": trends["delivery"],
        "committed_trend": trends["committed"],
        "overall": overalls["intraday"], "overall_delivery": overalls["delivery"],
        "overall_committed": overalls["committed"],
    }}


# ── Autopilot ─────────────────────────────────────────────────────────────────

class AutopilotRequest(BaseModel):
    enabled: bool
    mode: Optional[str] = "paper"     # "paper" | "backtest"


# Autopilot runs as its own microservice (autopilot-service:8015) which owns the
# paper + backtest training loops. The backend proxies status/control to it, and
# always writes the enable flags to Redis too so the toggle works even if the
# service is briefly restarting.

async def _autopilot_status() -> dict:
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.AUTOPILOT_SERVICE_URL}/status")
            if r.status_code == 200:
                return r.json().get("data", {})
    except Exception as exc:
        logger.debug("autopilot status proxy failed: %s", exc)
    # Fallback: at least report the flags from Redis so the UI stays accurate.
    from app.utils.redis_client import cache_get
    paper = (await cache_get("ai_engine:autopilot_enabled")) == "1"
    bt    = (await cache_get("ai_engine:autopilot_backtest_enabled")) == "1"
    timing = (await cache_get("ai_engine:autopilot_paper_timing")) or "normal"
    return {"paper": {"enabled": paper, "timing_mode": timing}, "backtest": {"enabled": bt}, "service_unavailable": True}


class TradeGateRequest(BaseModel):
    mode: str   # "strict" | "gentle" | "loose"


async def get_trade_gate_endpoint():
    """Current trade-gate mode + the available presets (for the dashboard)."""
    from app.api.sessions import get_trade_gate, TRADE_GATES
    mode = await get_trade_gate()
    return {"status": "success", "data": {
        "mode": mode,
        "options": [{"id": k, "label": v["label"], "desc": v["desc"]} for k, v in TRADE_GATES.items()],
    }}


class WatchlistConfigRequest(BaseModel):
    max: int = 6


async def get_watchlist_config():
    """Current intraday watchlist size (how many most-convicted picks are shown +
    graded for the signal score)."""
    from app.utils.redis_client import cache_get
    raw = await cache_get("ai_engine:watchlist_max")
    return {"status": "success", "data": {"max": int(raw) if raw else 6}}


async def set_watchlist_config(req: WatchlistConfigRequest):
    """Set how many most-convicted intraday picks the watchlist surfaces + grades
    (3–25). Applies on the next scan; trigger a Rescan to apply immediately."""
    n = max(3, min(25, int(req.max)))
    try:
        from app.utils.redis_client import cache_set
        await cache_set("ai_engine:watchlist_max", str(n), expire=86400 * 365)
    except Exception as exc:
        logger.warning("watchlist-config set failed: %s", exc)
    return {"status": "success", "data": {"max": n}}


async def set_trade_gate_endpoint(req: TradeGateRequest):
    """Switch how selective session entries are (applies to paper, replay & autopilot)."""
    from app.api.sessions import TRADE_GATES, TRADE_GATE_KEY, _gate_cache
    mode = req.mode if req.mode in TRADE_GATES else "gentle"
    try:
        from app.utils.redis_client import cache_set
        await cache_set(TRADE_GATE_KEY, mode, expire=86400 * 365)
        _gate_cache.update({"mode": mode, "ts": 0.0})   # force re-read next tick
    except Exception as exc:
        logger.warning("trade-gate set failed: %s", exc)
    return {"status": "success", "data": {"mode": mode, "applied": TRADE_GATES[mode]["label"]}}


async def get_llm_status():
    """Which LLM provider is active (Anthropic vs Ollama) and whether it responds."""
    from app.utils.llm_client import llm_status
    return {"status": "success", "data": await llm_status(probe=True)}


async def get_angel_status():
    """Angel One real-time feed status (configured? logged in? symbols live?)."""
    from app.utils.angel_client import get_angel_client
    client = get_angel_client()
    if not client:
        return {"status": "success", "data": {"configured": False,
                "note": "Set ANGEL_API_KEY / ANGEL_CLIENT_CODE / ANGEL_PIN / ANGEL_TOTP_SECRET to enable real-time broker data."}}
    return {"status": "success", "data": {"configured": True, **client.get_status()}}


async def get_autopilot():
    return {"status": "success", "data": await _autopilot_status()}


async def set_autopilot(req: AutopilotRequest):
    mode = req.mode if req.mode in ("paper", "backtest") else "paper"
    flag = "ai_engine:autopilot_backtest_enabled" if mode == "backtest" else "ai_engine:autopilot_enabled"
    # Write the flag directly (robust), and notify the service so it reacts now.
    try:
        from app.utils.redis_client import cache_set
        await cache_set(flag, "1" if req.enabled else "0", expire=86400 * 30)
    except Exception as exc:
        logger.warning("autopilot flag write failed: %s", exc)
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{settings.AUTOPILOT_SERVICE_URL}/control",
                              json={"mode": mode, "enabled": req.enabled})
    except Exception as exc:
        logger.debug("autopilot control proxy failed: %s", exc)
    return {"status": "success", "data": await _autopilot_status()}


async def reset_autopilot_cursor():
    """Reset the backtest autopilot's next trade date to the last trading day
    before today (proxied to the autopilot microservice)."""
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{settings.AUTOPILOT_SERVICE_URL}/backtest/reset-cursor")
            if r.status_code == 200:
                return {"status": "success", "data": (r.json().get("data") or {})}
    except Exception as exc:
        logger.warning("autopilot reset-cursor proxy failed: %s", exc)
    return {"status": "success", "data": await _autopilot_status()}


class BatchSizeRequest(BaseModel):
    batch_size: int


async def set_autopilot_batch_size(req: BatchSizeRequest):
    """Change the backtest autopilot's concurrent-sessions-per-batch (1–50),
    proxied to the autopilot microservice. Takes effect on the next batch."""
    n = max(1, min(50, int(req.batch_size)))
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{settings.AUTOPILOT_SERVICE_URL}/backtest/batch-size",
                                  json={"batch_size": n})
            if r.status_code == 200:
                return {"status": "success", "data": (r.json().get("data") or {})}
    except Exception as exc:
        logger.warning("autopilot batch-size proxy failed: %s", exc)
    return {"status": "success", "data": await _autopilot_status()}


class SpeedRequest(BaseModel):
    speed: int


async def set_autopilot_speed(req: SpeedRequest):
    """Change the backtest autopilot's replay speed (candles/step, 1–120),
    proxied to the autopilot microservice. Applies to newly started sessions."""
    n = max(1, min(120, int(req.speed)))
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{settings.AUTOPILOT_SERVICE_URL}/backtest/speed",
                                  json={"speed": n})
            if r.status_code == 200:
                return {"status": "success", "data": (r.json().get("data") or {})}
    except Exception as exc:
        logger.warning("autopilot speed proxy failed: %s", exc)
    return {"status": "success", "data": await _autopilot_status()}


class PaperTimingRequest(BaseModel):
    mode: str = "normal"     # "normal" | "aggressive"


async def set_autopilot_paper_timing(req: PaperTimingRequest):
    """Set the entry-timing mode for autopilot **paper** sessions. The autopilot
    reads this each tick, so new paper sessions open in the chosen mode (existing
    running sessions keep the mode they started with)."""
    mode = "aggressive" if req.mode == "aggressive" else "normal"
    try:
        from app.utils.redis_client import cache_get, cache_set
        prev = (await cache_get("ai_engine:autopilot_paper_timing")) or "normal"
        await cache_set("ai_engine:autopilot_paper_timing", mode, expire=86400 * 30)
        if mode != prev:
            await _log_system_event(
                f"Autopilot timing → {mode}", "trading",
                f"Autopilot paper entry-timing switched from {prev} to {mode}. "
                f"{'Looser entry triggers — expect more (and more marginal) trades.' if mode == 'aggressive' else 'Standard entry triggers restored.'}",
            )
    except Exception as exc:
        logger.warning("paper-timing write failed: %s", exc)
    return {"status": "success", "data": await _autopilot_status()}


# ── Learning curve (system getting smarter over time) ─────────────────────────

_VALID_SOURCES = {"PAPER", "REPLAY", "LIVE", "BACKTEST"}


async def learning_curve(source: str = "PAPER,LIVE,REPLAY", window: int = 50):
    """Learning curve as the system accumulates experience (trades ordered by time).

    Win-rate alone is misleading for an asymmetric-payoff strategy (small losses,
    large wins), so we return three aligned series plus a per-source breakdown:

      • cum_win_rate    — running win-rate over all trades so far (lagging)
      • roll_win_rate   — trailing-`window` win-rate (recency-sensitive: the real
                          "is it learning lately?" signal)
      • cum_equity      — cumulative sum of pnl_pct in % (the true profitability
                          curve; rises even when win-rate is < 50%)

    `source` is a comma list of PAPER/REPLAY/LIVE/BACKTEST. REPLAY (historical
    replays) usually dwarfs real PAPER/LIVE trades, so callers can isolate sources.
    """
    await _db_once()
    from app.database.postgres import engine
    from sqlalchemy import text
    from collections import deque

    srcs = [s.strip().upper() for s in (source or "").split(",") if s.strip().upper() in _VALID_SOURCES]
    if not srcs:
        srcs = ["PAPER", "LIVE", "REPLAY"]
    window = max(5, min(500, int(window or 50)))

    rows: list = []
    by_source: list[dict] = []
    events: list[dict] = []
    try:
        await _ensure_learning_events()
        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT outcome, pnl_pct, created_at, COALESCE(trade_source,'LIVE') src "
                "FROM trade_records WHERE outcome IN ('WIN','LOSS') "
                "AND COALESCE(trade_source,'LIVE') = ANY(:srcs) "
                "ORDER BY created_at ASC, id ASC"
            ), {"srcs": srcs})).fetchall()
            # Per-source summary across ALL sources (so the UI can show what it's filtering)
            summ = (await conn.execute(text(
                "SELECT COALESCE(trade_source,'LIVE') src, count(*) n, "
                "sum((outcome='WIN')::int) wins, avg(pnl_pct) avg_ret, "
                "avg(pnl_pct) FILTER (WHERE outcome='WIN') avg_win, "
                "avg(pnl_pct) FILTER (WHERE outcome='LOSS') avg_loss "
                "FROM trade_records WHERE outcome IN ('WIN','LOSS') GROUP BY 1 ORDER BY 2 DESC"
            ))).fetchall()
            for s in summ:
                n_s = int(s[1] or 0); wins_s = int(s[2] or 0)
                wr = wins_s / n_s if n_s else 0.0
                aw = float(s[4] or 0.0); al = float(s[5] or 0.0)
                by_source.append({
                    "source": s[0], "trades": n_s, "win_rate": round(wr, 4),
                    "avg_return": round(float(s[3] or 0.0) * 100, 3),
                    "avg_win": round(aw * 100, 3), "avg_loss": round(al * 100, 3),
                    # expectancy per trade in % — the metric that actually matters
                    "expectancy": round((wr * aw + (1 - wr) * al) * 100, 4),
                })
            ev = (await conn.execute(text(
                "SELECT occurred_at, title, category, detail FROM system_events ORDER BY occurred_at ASC"
            ))).fetchall()
            for e in ev:
                events.append({
                    "occurred_at": e[0].isoformat() if e[0] else None,
                    "title": e[1], "category": e[2] or "update", "detail": e[3] or "",
                })
    except Exception as exc:
        logger.warning("learning_curve failed: %s", exc)

    n = len(rows)
    points: list[dict] = []
    if n:
        step = max(1, n // 60)              # ~60 evenly-spaced samples
        win_q: deque = deque(maxlen=window) # rolling window of 1/0 outcomes
        ret_q: deque = deque(maxlen=window)
        cum_wins = cum_ret = 0.0
        for i, r in enumerate(rows, start=1):
            is_win = 1 if r[0] == "WIN" else 0
            pct = float(r[1] or 0.0)
            cum_wins += is_win
            cum_ret += pct
            win_q.append(is_win); ret_q.append(pct)
            if i % step == 0 or i == n:
                points.append({
                    "trade_no": i,
                    "ts": r[2].isoformat() if r[2] else None,
                    "date": r[2].strftime("%Y-%m-%d") if r[2] else None,
                    "cum_win_rate": round(cum_wins / i, 4),
                    "roll_win_rate": round(sum(win_q) / len(win_q), 4),
                    "roll_avg_return": round(sum(ret_q) / len(ret_q) * 100, 3),
                    "cum_equity": round(cum_ret * 100, 2),
                    "cum_avg_return": round(cum_ret / i * 100, 3),
                })
    return {"status": "success", "data": {
        "points": points, "total_trades": n, "sources": srcs, "window": window,
        "by_source": by_source, "events": events,
    }}


# ── System events overlay (correlate curve moves with what changed) ────────────

_learning_events_ready = False

# Known platform changes (seeded once) so the curve has context out of the box.
_SEED_EVENTS = [
    ("2026-06-01T00:00:00", "Full NSE universe scan", "scanner",
     "Scanner expanded to the complete ~1800+ NSE universe with rate-limiting; bulk REPLAY backfill begins."),
    ("2026-06-02T00:00:00", "Live paper trading enabled", "trading",
     "Autopilot paper sessions start executing intraday trades."),
    ("2026-06-10T00:00:00", "AI loss-learning loop", "learning",
     "LLM/rule post-mortems + active lessons fed into the agent prompt to avoid repeat losing setups."),
    ("2026-06-14T00:00:00", "Per-session entry-timing mode", "trading",
     "Normal/Aggressive entry timing added to paper/backtest sessions."),
    ("2026-06-16T00:00:00", "Autopilot aggressive timing", "trading",
     "Normal/Aggressive entry-timing toggle wired into autopilot paper trading."),
]


async def _ensure_learning_events() -> None:
    global _learning_events_ready
    if _learning_events_ready:
        return
    from app.database.postgres import engine
    from sqlalchemy import text
    from datetime import datetime
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS system_events ("
            "id SERIAL PRIMARY KEY, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "title TEXT NOT NULL, category TEXT DEFAULT 'update', detail TEXT DEFAULT '')"
        ))
        cnt = (await conn.execute(text("SELECT count(*) FROM system_events"))).scalar() or 0
        if not cnt:
            for occ, title, cat, detail in _SEED_EVENTS:
                await conn.execute(text(
                    "INSERT INTO system_events (occurred_at, title, category, detail) "
                    "VALUES (:o, :t, :c, :d)"
                ), {"o": datetime.fromisoformat(occ), "t": title, "c": cat, "d": detail})
    _learning_events_ready = True


async def _log_system_event(title: str, category: str = "update", detail: str = "") -> None:
    """Append a system-update marker to the learning curve. Best-effort: never
    raises into the caller (a failed annotation must not break the real action)."""
    try:
        await _ensure_learning_events()
        from app.database.postgres import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO system_events (title, category, detail) VALUES (:t, :c, :d)"
            ), {"t": title, "c": category, "d": detail})
    except Exception as exc:
        logger.warning("log_system_event failed: %s", exc)


class LearningEventRequest(BaseModel):
    title:       str
    detail:      str = ""
    category:    str = "update"
    occurred_at: Optional[str] = None   # ISO; defaults to now()


async def list_learning_events():
    """System-update markers shown on the learning curve."""
    await _db_once()
    await _ensure_learning_events()
    from app.database.postgres import engine
    from sqlalchemy import text
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT id, occurred_at, title, category, detail FROM system_events ORDER BY occurred_at ASC"
        ))).fetchall()
    return {"status": "success", "events": [{
        "id": r[0], "occurred_at": r[1].isoformat() if r[1] else None,
        "title": r[2], "category": r[3] or "update", "detail": r[4] or "",
    } for r in rows]}


async def add_learning_event(req: LearningEventRequest):
    """Log a system change so its effect on the curve can be seen."""
    await _db_once()
    await _ensure_learning_events()
    from app.database.postgres import engine
    from sqlalchemy import text
    from datetime import datetime
    async with engine.begin() as conn:
        if req.occurred_at:
            try:
                occ_dt = datetime.fromisoformat(req.occurred_at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="occurred_at must be ISO-8601")
            rid = (await conn.execute(text(
                "INSERT INTO system_events (occurred_at, title, category, detail) "
                "VALUES (:o, :t, :c, :d) RETURNING id"
            ), {"o": occ_dt, "t": req.title, "c": req.category, "d": req.detail})).scalar()
        else:
            rid = (await conn.execute(text(
                "INSERT INTO system_events (title, category, detail) "
                "VALUES (:t, :c, :d) RETURNING id"
            ), {"t": req.title, "c": req.category, "d": req.detail})).scalar()
    return {"status": "success", "id": rid}


# ── Pattern Memory ────────────────────────────────────────────────────────────

class SeedMemoryRequest(BaseModel):
    symbols:      Optional[list[str]] = None   # default: KNOWN_STOCKS universe
    lookback_days: int = 365
    horizon:       int = 3      # bars ahead used to label the outcome
    stride:        int = 1      # sample every Nth window
    max_per_symbol: int = 400


async def memory_stats():
    """Size + win-rate breakdown of the Pattern Memory bank (for the UI)."""
    await _db_once()
    from app.agents import get_memory
    return await get_memory().stats()


async def memory_purge(sources: str = "REPLAY"):
    """Delete memory cases by source label so the bank can rebuild from clean data.

    sources: comma-separated, e.g. 'REPLAY' or 'REPLAY,BACKTEST'.
    Intended for resetting after a bug-fix that invalidated old training runs.
    LIVE cases are never affected regardless of what is passed.
    """
    await _db_once()
    from sqlalchemy import text
    from app.database.postgres import engine as pg_engine
    from app.agents import get_memory

    allowed   = {"REPLAY", "BACKTEST", "PAPER"}
    protected = {"LIVE"}
    to_delete = [s.strip().upper() for s in sources.split(",")
                 if s.strip().upper() in allowed and s.strip().upper() not in protected]
    if not to_delete:
        return {"status": "error", "detail": "No valid purgeable sources specified (allowed: REPLAY, BACKTEST, PAPER)"}

    deleted_per_source: dict[str, int] = {}
    async with pg_engine.begin() as conn:
        for src in to_delete:
            cnt = (await conn.execute(text("SELECT COUNT(*) FROM pattern_memory WHERE source=:s"), {"s": src})).scalar() or 0
            await conn.execute(text("DELETE FROM pattern_memory WHERE source=:s"), {"s": src})
            deleted_per_source[src] = int(cnt)

    # Force cache refresh so the in-process k-NN matrix reflects the deletions immediately.
    get_memory()._loaded_at = 0.0

    total = sum(deleted_per_source.values())
    return {"status": "success", "data": {"deleted": deleted_per_source, "total": total}}


async def memory_query(req: AnalyzeRequest):
    """Inspect what the memory bank recalls for the situation in `candles`."""
    await _db_once()
    from app.agents import get_memory
    from app.agents.fingerprint import build_fingerprint, classify_regime
    fp = build_fingerprint(req.candles)
    if fp is None:
        return {"status": "error", "detail": "need ≥15 candles", "data": None}
    regime = classify_regime(req.candles)
    res = await get_memory().query(fp, symbol=req.symbol.upper(), regime=regime)
    return {"status": "success", "data": {**res, "regime": regime}}


async def memory_sweep(background: bool = True):
    """Refresh the BACKTEST memory from fresh real backtests across the watchlist.

    Replaces (not appends) backtest cases so it stays bounded; LIVE cases are
    preserved. Runs automatically nightly — this triggers it on demand."""
    await _db_once()
    import asyncio
    from app.agents.memory_sweep import run_memory_sweep, is_running
    if is_running():
        return {"status": "already_running"}
    if background:
        asyncio.create_task(run_memory_sweep(trigger="manual"))
        return {"status": "started"}
    return await run_memory_sweep(trigger="manual")


async def memory_sweep_status():
    """Last sweep summary + whether one is currently running."""
    from app.agents.memory_sweep import get_last_sweep, is_running
    return {"running": is_running(), "last": get_last_sweep()}


# ── Pattern Recognition Model (dedicated, continuously-learning) ───────────────

class PatternTrainRequest(BaseModel):
    symbols:       Optional[list[str]] = None
    lookback_days: int = 365
    horizon:       int = 3       # bars ahead used to label the pattern's outcome
    stride:        int = 1


async def pattern_model_train(req: PatternTrainRequest, background: bool = True):
    """Train the pattern-recognition model from backtest history — patterns ONLY
    (fingerprint → realised forward move). Keeps the recogniser getting smarter."""
    await _db_once()
    import asyncio
    from app.agents.pattern_model import train_pattern_model, is_training
    if is_training():
        return {"status": "already_running"}
    kw = dict(symbols=req.symbols, lookback_days=req.lookback_days,
              horizon=req.horizon, stride=req.stride, trigger="manual")
    if background:
        asyncio.create_task(train_pattern_model(**kw))
        return {"status": "started"}
    return await train_pattern_model(**kw)


async def pattern_model_status():
    """Training state + the model's current accuracy."""
    from app.agents import get_pattern_model
    from app.agents.pattern_model import is_training, get_last_train
    stats = await get_pattern_model().stats()
    return {"running": is_training(), "last_train": get_last_train(), "model": stats}


async def pattern_model_curve(limit: int = 200):
    """The model's accuracy as it has learned (for the 'getting smarter' chart)."""
    from app.agents import get_pattern_model
    pts = await get_pattern_model().curve(limit=limit)
    return {"status": "success", "data": {"points": pts}}


async def pattern_model_weights():
    """The learned weights — the scanner pulls these once per sweep to score each
    pattern locally and gate the high-conviction tier on the model's agreement."""
    from app.agents import get_pattern_model
    m = get_pattern_model()
    await m.init_db()
    return {"status": "success", "data": m.weights_payload()}


async def pattern_model_predict(req: AnalyzeRequest):
    """What does the pattern model say about this candle window's *pattern* alone?"""
    from app.agents import get_pattern_model
    pred = get_pattern_model().predict_candles(req.candles)
    if pred is None:
        return {"status": "error", "detail": "not enough candles for a pattern fingerprint"}
    return {"status": "success", "data": pred}


async def pattern_model_grade(req: AnalyzeRequest):
    """Graded pattern signal (A/B/C/D) from the unified pattern engine — model P(up)
    blended with the memory bank's win-rate, plus a Monte-Carlo path forecast
    (projected target/stop + uncertainty). The same grading used to gate trades."""
    await _db_once()
    from app.agents import get_pattern_engine
    sig = await get_pattern_engine().signal(
        req.candles, (req.symbol or "").upper() or None, with_forecast=True)
    return {"status": "success", "data": sig}


async def pattern_model_forecast(req: AnalyzeRequest):
    """Monte-Carlo path forecast for a candle window — projected return path with an
    uncertainty band, data-driven target/stop (expected favourable/adverse
    excursion), P(up) and P(target-before-stop). CPU-only, no GPU/model weights."""
    from app.agents import get_path_forecaster
    horizon = None
    try:
        if req.context and req.context.get("horizon") is not None:
            horizon = int(req.context.get("horizon"))
    except (TypeError, ValueError):
        horizon = None
    fc = get_path_forecaster().forecast(req.candles, horizon=horizon)
    return {"status": "success", "data": fc}


# ── AI model registry (independent enable/weight per model) ───────────────────

class ModelConfigRequest(BaseModel):
    name:    str
    enabled: Optional[bool] = None
    weight:  Optional[float] = None
    clear_weight: bool = False     # set weight back to learned/default


async def list_models():
    """All independent AI models with their enable flag + weight override and
    human-facing metadata, for the AI Models control panel."""
    from app.agents.registry import get_registry, META
    reg = await get_registry(force=True)
    models = []
    for name, cfg in reg.items():
        m = META.get(name, {})
        entry = {"name": name, "enabled": cfg.get("enabled", True),
                 "weight": cfg.get("weight"),
                 "label": m.get("label", name), "kind": m.get("kind", "model"),
                 "desc": m.get("desc", "")}
        if name == "gbm":
            try:
                from app.agents import get_gbm_model
                gm = get_gbm_model(); await gm.init_db()
                entry["trained"] = gm.is_trained
                entry["meta"] = gm.meta
            except Exception:
                logger.warning("gbm model status check failed", exc_info=True)
                entry["trained"] = False
        models.append(entry)
    return {"status": "success", "data": {"models": models}}


async def update_model(req: ModelConfigRequest):
    """Enable/disable a model or pin/clear its vote-weight override at runtime."""
    from app.agents.registry import set_model, DEFAULTS
    if req.name not in DEFAULTS:
        raise HTTPException(404, f"unknown model '{req.name}'")
    weight_arg = None if req.clear_weight else (req.weight if req.weight is not None else ...)
    reg = await set_model(req.name, enabled=req.enabled, weight=weight_arg)
    return {"status": "success", "data": {"name": req.name, "config": reg.get(req.name)}}


async def gbm_train(max_symbols: int = 250, horizon: int = 3, lookback_days: int = 365):
    """Train the Gradient-Boosted P(up) model on backfill (fingerprint → realised
    forward return) samples. Rotates through the universe across runs."""
    from app.agents.pattern_model import train_gbm_model
    res = await train_gbm_model(max_symbols=max_symbols, horizon=horizon,
                                lookback_days=lookback_days, trigger="manual")
    return {"status": "success", "data": res}


async def gbm_status():
    from app.agents import get_gbm_model
    gm = get_gbm_model()
    await gm.init_db()
    return {"status": "success", "data": {"trained": gm.is_trained, "meta": gm.meta}}


async def memory_seed(req: SeedMemoryRequest):
    """Bulk-seed the memory bank by replaying historical daily candles.

    For each sliding window we fingerprint the situation, look `horizon` bars
    ahead to measure the realised forward return, label the action by its sign,
    and store the case. This is the system's 'study' phase — it walks through
    history once so it starts live having already 'seen' thousands of setups.
    """
    await _db_once()
    from datetime import datetime, timedelta
    from app.agents import get_memory
    from app.agents.fingerprint import build_fingerprint, classify_regime
    from app.api.agent import KNOWN_STOCKS
    from app.utils.candle_utils import parse_candles, simulate_daily_candles
    from app.utils.groww_client import get_groww_client

    symbols = [s.upper() for s in (req.symbols or list(KNOWN_STOCKS.keys()))]
    horizon = max(1, req.horizon)
    stride  = max(1, req.stride)
    groww   = get_groww_client()

    total_inserted = 0
    per_symbol: dict[str, int] = {}

    for sym in symbols:
        # Fetch daily candles (Groww, simulated fallback)
        candles: list[dict] = []
        try:
            end   = datetime.now()
            start = end - timedelta(days=req.lookback_days)
            if groww:
                raw = await groww.get_historical(sym, 1440, start, end)
                if raw and len(raw) > 40:
                    candles = parse_candles(raw, date_key="timestamp")
            if not candles:
                candles = simulate_daily_candles(sym, start, end, date_key="timestamp")
        except Exception as exc:
            logger.warning("seed fetch failed for %s: %s", sym, exc)
            continue

        if len(candles) < 40:
            continue

        cases: list[dict] = []
        # need ≥15 lookback for fingerprint, and `horizon` lookahead for label
        for i in range(15, len(candles) - horizon, stride):
            window = candles[: i + 1]
            fp = build_fingerprint(window)
            if fp is None:
                continue
            entry = float(candles[i]["close"])
            future = float(candles[i + horizon]["close"])
            fwd_ret = (future - entry) / entry * 100 if entry else 0.0
            # Label: would a long have paid off over the horizon?
            if fwd_ret > 0.3:
                action = "BUY"
            elif fwd_ret < -0.3:
                action = "SELL"
            else:
                action = "HOLD"
            cases.append({
                "symbol": sym, "fingerprint": fp, "action": action,
                "entry_price": entry, "exit_price": future,
                # For a SELL/short label the "win" is a downward move → flip sign
                "pnl_pct": fwd_ret if action != "SELL" else -fwd_ret,
                "regime": classify_regime(window), "source": "BACKTEST",
            })
            if len(cases) >= req.max_per_symbol:
                break

        inserted = await get_memory().add_cases_bulk(cases)
        per_symbol[sym] = inserted
        total_inserted += inserted

    logger.info("Pattern memory seeded",
                extra={"log_type": "ai_engine", "event": "memory_seed",
                       "symbols": len(symbols), "inserted": total_inserted})
    return {
        "status": "success",
        "data": {
            "symbols_processed": len(symbols),
            "total_inserted": total_inserted,
            "per_symbol": per_symbol,
        },
    }


# ── Sentiment pipeline ────────────────────────────────────────────────────────

async def sentiment_refresh(symbol: str, force: bool = False):
    """Fetch fresh news headlines for a symbol, analyse with LLM, write to Redis.

    The SentimentAgent reads `ai_engine:sentiment:{SYMBOL}` on every analyze call.
    This endpoint lets the UI (or a scheduler) pre-warm that key so the agent
    has a real signal to vote on.

    force=true skips the 15-minute cache TTL and re-fetches unconditionally.
    """
    from app.agents.sentiment_pipeline import run_pipeline
    result = await run_pipeline(symbol.upper(), force=force)
    return {
        "status": "success",
        "data": {
            "symbol":          symbol.upper(),
            "sentiment":       result.get("sentiment"),
            "score":           result.get("score"),
            "confidence":      result.get("confidence"),
            "catalyst":        result.get("catalyst"),
            "summary":         result.get("summary"),
            "headlines_count": result.get("headlines_count", 0),
            "headlines":       result.get("headlines", []),
            "provider":        result.get("provider"),
            "cached":          not force,
        },
    }


async def sentiment_historical_bulk(payload: dict):
    """Pre-fetch historical news sentiment for a list of symbols on a specific date.

    Used by the backtest autopilot before starting a queue: it ranks all candidate
    symbols by sentiment score for the backtest date so only the most bullish/
    high-conviction stocks are selected.

    Request body: {"symbols": ["SBIN", "HDFCBANK", ...], "date": "YYYY-MM-DD"}
    Response: {"data": {"SBIN": {sentiment, score, confidence, catalyst, ...}, ...}}
    """
    import asyncio
    from app.agents.sentiment_pipeline import run_pipeline_for_date

    symbols: list[str] = [s.upper() for s in (payload.get("symbols") or []) if s]
    date: str = (payload.get("date") or "").strip()
    if not date or not symbols:
        return {"status": "error", "detail": "symbols and date are required"}

    async def _fetch(sym: str) -> tuple[str, dict]:
        try:
            result = await run_pipeline_for_date(sym, date)
            return sym, result
        except Exception as exc:
            logger.debug("historical sentiment failed for %s/%s: %s", sym, date, exc)
            return sym, {"sentiment": "neutral", "score": 0.0, "confidence": 0.0,
                         "catalyst": "", "headlines_count": 0}

    results = await asyncio.gather(*[_fetch(sym) for sym in symbols])
    data = {sym: res for sym, res in results}
    return {"status": "success", "date": date, "data": data}


async def sentiment_historical_read(symbol: str, date: str):
    """Read (or lazily fetch) cached historical sentiment for a symbol on a given date."""
    from app.agents.sentiment_pipeline import run_pipeline_for_date
    result = await run_pipeline_for_date(symbol.upper(), date)
    return {"status": "success", "data": result}


async def sentiment_read(symbol: str):
    """Read the current cached sentiment for a symbol (no re-fetch)."""
    import json
    from app.utils.redis_client import cache_get
    try:
        raw = await cache_get(f"ai_engine:sentiment:{symbol.upper()}")
        if raw:
            return {"status": "success", "data": json.loads(raw)}
    except Exception as exc:
        logger.debug("sentiment read failed for %s: %s", symbol, exc)
    return {"status": "success", "data": {"sentiment": None, "headlines_count": 0}}


# ── Agent-decision drill-downs (Orders trace popup + per-agent page) ───────────

async def trade_agent_detail(session_id: str):
    """Rich per-agent decision for the trade a session produced: each agent's
    action, weight, confidence and reasoning AT DECISION TIME (from the executed
    entry in session_decisions), plus every agent's lifetime per-action accuracy
    (from the learning system, real-outcomes-only). Powers the clickable agent
    cards in the Orders execution-trace popup."""
    from sqlalchemy import text
    from app.database.postgres import engine
    agents: list[dict] = []
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text("""
                SELECT agents, symbol, candle_time, price
                FROM session_decisions
                WHERE session_id = :sid AND executed = TRUE AND action = 'BUY'
                ORDER BY created_at DESC LIMIT 1
            """), {"sid": session_id})).fetchone()
        if row and row[0]:
            raw = row[0] if isinstance(row[0], list) else json.loads(row[0])
            agents = [
                {"agent": a.get("agent") or a.get("agent_name"),
                 "action": a.get("action"),
                 "weight": round(float(a.get("weight") or 0), 3),
                 "confidence": round(float(a.get("confidence") or 0), 3),
                 "reasoning": a.get("reasoning") or ""}
                for a in raw
            ]
    except Exception as exc:
        logger.debug("trade_agent_detail decision lookup failed: %s", exc)

    # Lifetime per-action accuracy (real outcomes only — see learning.py filter).
    accuracy: dict = {}
    try:
        from app.agents import get_learning
        perf = await get_learning().get_performance()
        for a in perf:
            name = a.get("agent") or a.get("agent_name")
            if name:
                accuracy[name] = {
                    "weight": a.get("weight"),
                    "by_action": a.get("by_action") or [],
                }
    except Exception as exc:
        logger.debug("trade_agent_detail accuracy lookup failed: %s", exc)

    return {"status": "success", "data": {"agents": agents, "accuracy": accuracy}}


async def agent_trades(agent: str, limit: int = 100):
    """Per-agent drill-down: every EXECUTED trade this agent voted on — its vote,
    confidence and weight at the time, the ensemble's action, and whether the
    agent was RIGHT (BUY vote correct when the trade won; SELL/HOLD correct when
    it lost/flat). Real paper/live + replay trades that produced a record."""
    from sqlalchemy import text
    from app.database.postgres import engine
    out: list[dict] = []
    summary = {"n": 0, "correct": 0, "by_action": {}}
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT d.symbol, d.candle_time, d.created_at::date AS d,
                       d.action AS ensemble_action, d.price,
                       sig, tr.outcome,
                       (tr.exit_price - tr.entry_price) / NULLIF(tr.entry_price,0) * 100 AS pnl_pct
                FROM session_decisions d
                JOIN trade_records tr ON tr.session_id = d.session_id
                CROSS JOIN LATERAL jsonb_array_elements(d.agents) AS sig
                WHERE d.executed = TRUE
                  AND (sig->>'agent' = :ag OR sig->>'agent_name' = :ag)
                  AND tr.outcome IN ('WIN','LOSS')
                ORDER BY d.created_at DESC LIMIT :lim
            """), {"ag": agent, "lim": limit})).fetchall()
        for r in rows:
            sig = r[5] if isinstance(r[5], dict) else json.loads(r[5])
            vote = sig.get("action")
            won = (r[6] == "WIN")
            correct = won if vote == "BUY" else (not won)
            rec = {
                "symbol": r[0], "time": r[1], "date": str(r[2]),
                "ensemble_action": r[3], "price": float(r[4] or 0),
                "vote": vote,
                "confidence": round(float(sig.get("confidence") or 0), 3),
                "weight": round(float(sig.get("weight") or 0), 3),
                "outcome": r[6], "pnl_pct": round(float(r[7] or 0), 2),
                "correct": bool(correct),
            }
            out.append(rec)
            summary["n"] += 1
            summary["correct"] += int(correct)
            ba = summary["by_action"].setdefault(vote, {"n": 0, "correct": 0})
            ba["n"] += 1; ba["correct"] += int(correct)
    except Exception as exc:
        logger.debug("agent_trades lookup failed: %s", exc)
    summary["accuracy"] = round(summary["correct"] / summary["n"], 3) if summary["n"] else None
    for act, v in summary["by_action"].items():
        v["accuracy"] = round(v["correct"] / v["n"], 3) if v["n"] else None
    return {"status": "success", "data": {"agent": agent, "summary": summary, "trades": out}}


# ── Helper ────────────────────────────────────────────────────────────────────

def _candle_time(candles: list[dict]) -> str:
    if not candles:
        return "unknown"
    last = candles[-1]
    ts   = last.get("timestamp") or last.get("time")
    if ts:
        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        try:
            return datetime.fromtimestamp(int(ts), tz=IST).strftime("%H:%M")
        except Exception:
            pass
    return "unknown"
