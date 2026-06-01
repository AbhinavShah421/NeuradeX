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
async def get_watchlist():
    """The live AI watchlist produced by the stock-scanner service (read from Redis)."""
    import json
    from app.utils.redis_client import cache_get
    try:
        raw = await cache_get("ai_engine:watchlist")
        if raw:
            return {"status": "success", "data": json.loads(raw)}
    except Exception as exc:
        logger.debug("watchlist read failed: %s", exc)
    return {"status": "success", "data": {"updated_at": None, "scanned": 0, "universe": 0, "items": []}}


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


@router.get("/llm-status")
async def get_llm_status():
    """Which LLM provider is active (Anthropic vs Ollama) and whether it responds."""
    from app.utils.llm_client import llm_status
    return {"status": "success", "data": await llm_status(probe=True)}


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
            rows = (await conn.execute(text("""
                SELECT outcome, pnl_pct, created_at FROM trade_records
                WHERE outcome IN ('WIN','LOSS') ORDER BY created_at ASC, id ASC
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
