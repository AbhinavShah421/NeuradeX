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
    from app.agents import get_learning
    await get_learning().init_db()

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

    # Persist prediction
    await learning.store_prediction(decision, _candle_time(req.candles), context, rl_state)

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
    """Record trade outcome — triggers weight update + RL Q-table update."""
    await _db_once()
    from app.agents import get_learning, get_rl_agent
    from app.database.postgres import engine as pg_engine
    from sqlalchemy import text

    learning = get_learning()
    reward   = await learning.record_outcome(
        req.prediction_id, req.symbol,
        req.entry_price, req.exit_price,
        req.pnl, req.pnl_pct,
    )

    # Update RL Q-table
    try:
        async with pg_engine.begin() as conn:
            row = (await conn.execute(
                text("SELECT rl_state, agent_signals FROM ai_engine_predictions WHERE prediction_id=:pid"),
                {"pid": req.prediction_id},
            )).fetchone()

        if row and row[0] is not None:
            import json
            state   = row[0]
            signals = json.loads(row[1])
            rl_sig  = next((s for s in signals if s["agent"] == "rl"), None)
            if rl_sig:
                from app.agents.rl_agent import ACTIONS
                action_idx = ACTIONS.index(rl_sig["action"]) if rl_sig["action"] in ACTIONS else 2
                # next_state: use same state as proxy (no live next candles here)
                await get_rl_agent().update(state, action_idx, reward, state)
    except Exception as exc:
        logger.warning("RL update skipped: %s", exc)

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
