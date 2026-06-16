"""AI Engine REST API — analyze, record outcomes, performance, history."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.utils.elk_logger import get_logger

router = APIRouter()
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

@router.post("/analyze")
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
        pass

    # Fingerprint for pattern-memory learning
    fingerprint = None
    try:
        from app.agents.fingerprint import build_fingerprint
        fingerprint = build_fingerprint(req.candles)
    except Exception:
        pass

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


@router.post("/outcome")
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


@router.get("/performance")
async def get_performance():
    """Per-agent weight and accuracy stats."""
    await _db_once()
    from app.agents import get_learning
    return await get_learning().get_performance()


@router.get("/history")
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


@router.get("/weights")
async def get_weights():
    """Current agent weights."""
    await _db_once()
    from app.agents import get_learning
    return await get_learning().get_weights()


@router.get("/learning-summary")
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

@router.get("/watchlist")
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
                    pass
            return {"status": "success", "data": data}
    except Exception as exc:
        logger.debug("watchlist read failed: %s", exc)
    return {"status": "success", "data": {"updated_at": None, "scanned": 0, "universe": 0, "items": []}}


@router.get("/ranked")
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
                    pass
            return {"status": "success", "data": {**data, "items": items, "returned": len(items)}}
    except Exception as exc:
        logger.debug("ranked board read failed: %s", exc)
    return {"status": "success", "data": {"updated_at": None, "scanned": 0, "universe": 0, "items": [], "returned": 0}}


@router.post("/watchlist/scan")
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


@router.get("/scan-status")
async def scan_status():
    """Centralized scan status (shared by Dashboard / Predictions / Portfolio):
    whether a sweep is running, progress, and when it last completed. Single source
    of truth so a rescan started on one page disables rescan everywhere."""
    import httpx, json
    from app.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.SCANNER_SERVICE_URL}/status")
            if r.status_code == 200:
                d = (r.json() or {}).get("data", {})
                return {"status": "success", "data": {
                    "scanning": bool(d.get("scanning")),
                    "running": bool(d.get("running")),
                    "scanned": d.get("scanned", 0),
                    "universe": d.get("universe", 0),
                    "candidates": d.get("candidates", 0),
                    "last_scan": d.get("last_scan"),
                    "market_regime": d.get("market_regime"),
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
        pass
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

_FEEDBACK_URL = "http://feedback-service:8012"
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
        pass
    return lessons


@router.post("/loss-learning/run")
async def loss_learning_run(limit: int = 60, max_new: int = 15):
    """Analyse recent losing trades that don't yet have a post-mortem, store the
    AI explanations, and refresh the aggregated lessons."""
    import httpx, json as _json
    from sqlalchemy import text
    from app.database.postgres import engine

    # 1. Pull losing trades from the feedback-service.
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{_FEEDBACK_URL}/trades", params={"limit": limit})
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


@router.get("/loss-learning/postmortems")
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


@router.get("/loss-learning/lessons")
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
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (eval_date, symbol)
);
"""


@router.post("/scan-feedback")
async def scan_feedback(req: ScanFeedback):
    """Receive the scanner's post-market grade and persist each pick's outcome so
    it feeds the system's learning record (and the signal-score history)."""
    from datetime import date
    from sqlalchemy import text
    from app.database.postgres import engine
    try:
        eval_date = date.fromisoformat(req.date)
    except (ValueError, TypeError):
        eval_date = date.today()
    inserted = 0
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_SCAN_EVAL_DDL))
            for g in req.results:
                await conn.execute(text("""
                    INSERT INTO scan_evaluations
                      (eval_date, symbol, action, predicted_confidence,
                       predicted_signal_score, day_return_pct, realized_return_pct, correct)
                    VALUES (:d,:sym,:act,:pc,:ps,:dr,:rr,:ok)
                    ON CONFLICT (eval_date, symbol) DO UPDATE SET
                      action=:act, predicted_confidence=:pc, predicted_signal_score=:ps,
                      day_return_pct=:dr, realized_return_pct=:rr, correct=:ok
                """), {
                    "d": eval_date, "sym": g.get("symbol"), "act": g.get("action"),
                    "pc": g.get("predicted_confidence"), "ps": g.get("predicted_signal_score"),
                    "dr": g.get("day_return_pct"), "rr": g.get("realized_return_pct"),
                    "ok": bool(g.get("correct")),
                })
                inserted += 1
    except Exception as exc:
        logger.warning("scan_feedback persist failed: %s", exc)
    logger.info("scan feedback %s: %d picks, accuracy %.0f%%",
                req.date, req.picks, (req.accuracy or 0) * 100)
    return {"status": "recorded", "stored": inserted, "date": req.date}


@router.get("/scan-evaluation")
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

    trend: list[dict] = []
    overall = {"days": 0, "accuracy": None, "picks": 0}
    try:
        async with engine.begin() as conn:
            await conn.execute(text(_SCAN_EVAL_DDL))
            rows = (await conn.execute(text("""
                SELECT eval_date,
                       AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) AS acc,
                       AVG(realized_return_pct) AS avg_ret,
                       COUNT(*) AS n
                FROM scan_evaluations
                GROUP BY eval_date ORDER BY eval_date ASC
            """))).fetchall()
            for r in rows:
                trend.append({
                    "date": r[0].strftime("%Y-%m-%d") if r[0] else None,
                    "accuracy": round(float(r[1]), 4),
                    "avg_realized_return_pct": round(float(r[2] or 0.0), 2),
                    "picks": int(r[3]),
                })
            agg = (await conn.execute(text("""
                SELECT AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END), COUNT(*) FROM scan_evaluations
            """))).fetchone()
            if agg and agg[1]:
                overall = {"days": len(trend), "accuracy": round(float(agg[0]), 4), "picks": int(agg[1])}
    except Exception as exc:
        logger.debug("scan eval trend failed: %s", exc)

    return {"status": "success", "data": {"latest": latest, "trend": trend, "overall": overall}}


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
    return {"paper": {"enabled": paper}, "backtest": {"enabled": bt}, "service_unavailable": True}


class TradeGateRequest(BaseModel):
    mode: str   # "strict" | "gentle" | "loose"


@router.get("/trade-gate")
async def get_trade_gate_endpoint():
    """Current trade-gate mode + the available presets (for the dashboard)."""
    from app.api.sessions import get_trade_gate, TRADE_GATES
    mode = await get_trade_gate()
    return {"status": "success", "data": {
        "mode": mode,
        "options": [{"id": k, "label": v["label"], "desc": v["desc"]} for k, v in TRADE_GATES.items()],
    }}


@router.post("/trade-gate")
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


@router.get("/llm-status")
async def get_llm_status():
    """Which LLM provider is active (Anthropic vs Ollama) and whether it responds."""
    from app.utils.llm_client import llm_status
    return {"status": "success", "data": await llm_status(probe=True)}


@router.get("/angel-status")
async def get_angel_status():
    """Angel One real-time feed status (configured? logged in? symbols live?)."""
    from app.utils.angel_client import get_angel_client
    client = get_angel_client()
    if not client:
        return {"status": "success", "data": {"configured": False,
                "note": "Set ANGEL_API_KEY / ANGEL_CLIENT_CODE / ANGEL_PIN / ANGEL_TOTP_SECRET to enable real-time broker data."}}
    return {"status": "success", "data": {"configured": True, **client.get_status()}}


@router.get("/autopilot")
async def get_autopilot():
    return {"status": "success", "data": await _autopilot_status()}


@router.post("/autopilot")
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


@router.post("/autopilot/reset-cursor")
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


# ── Learning curve (system getting smarter over time) ─────────────────────────

@router.get("/learning-curve")
async def learning_curve():
    """Cumulative win-rate as the system accumulates experience (trades ordered by
    time). Shows the system stabilising/improving as it learns from paper trading,
    sessions and backtests — and extends as the autopilot trades more."""
    await _db_once()
    from app.database.postgres import engine
    from sqlalchemy import text
    try:
        async with engine.begin() as conn:
            # Intraday system → curve reflects intraday trades (paper/replay/live),
            # not the multi-day strategy backtester.
            rows = (await conn.execute(text("""
                SELECT outcome, pnl_pct, created_at FROM trade_records
                WHERE outcome IN ('WIN','LOSS')
                  AND COALESCE(trade_source,'LIVE') IN ('PAPER','REPLAY','LIVE')
                ORDER BY created_at ASC, id ASC
            """))).fetchall()
    except Exception as exc:
        logger.warning("learning_curve failed: %s", exc)
        rows = []

    n = len(rows)
    points: list[dict] = []
    if n:
        # ~40 evenly-spaced samples of the running cumulative win-rate
        step = max(1, n // 40)
        cum_wins = cum_ret = 0.0
        for i, r in enumerate(rows, start=1):
            if r[0] == "WIN":
                cum_wins += 1
            cum_ret += float(r[1] or 0.0)
            if i % step == 0 or i == n:
                points.append({
                    "trade_no": i,
                    "cum_win_rate": round(cum_wins / i, 4),
                    "cum_avg_return": round(cum_ret / i * 100, 2),
                    "date": r[2].strftime("%Y-%m-%d") if r[2] else None,
                })
    return {"status": "success", "data": {"points": points, "total_trades": n}}


# ── Pattern Memory ────────────────────────────────────────────────────────────

class SeedMemoryRequest(BaseModel):
    symbols:      Optional[list[str]] = None   # default: KNOWN_STOCKS universe
    lookback_days: int = 365
    horizon:       int = 3      # bars ahead used to label the outcome
    stride:        int = 1      # sample every Nth window
    max_per_symbol: int = 400


@router.get("/memory/stats")
async def memory_stats():
    """Size + win-rate breakdown of the Pattern Memory bank (for the UI)."""
    await _db_once()
    from app.agents import get_memory
    return await get_memory().stats()


@router.post("/memory/query")
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


@router.post("/memory/sweep")
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


@router.get("/memory/sweep/status")
async def memory_sweep_status():
    """Last sweep summary + whether one is currently running."""
    from app.agents.memory_sweep import get_last_sweep, is_running
    return {"running": is_running(), "last": get_last_sweep()}


@router.post("/memory/seed")
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
