"""Publish the backend ensemble's decision to `ensemble.decision`.

This is the bridge that makes the intended chain real:

    12 in-process vote agents
      -> backend ensemble        (aggregation: directional contest, memory gate,
                                  learned weights, per-action lift)
      -> ensemble.decision       <- this module
      -> ensemble-engine         (MLflow meta-gate + calibration)
      -> risk-engine             (sizing / vetoes)
      -> trade-executor          (paper by default)

Why the backend aggregates and not ensemble-engine: every tuned behaviour and
every learning loop already lives here and writes to this service's database.
Re-implementing aggregation in ensemble-engine is what produced two divergent
ensembles in the first place — one of which emitted 100% HOLD for its entire
life because its confidence ceiling (0.560) sat below its own trade gate (0.60).

DEFAULT OFF. `ENSEMBLE_PUBLISH_ENABLED=1` arms it. It is off because
risk-engine gates on `weightedConfidence >= 0.60`, while this ensemble's
confidence saturates at 0.95 whenever no agent votes the opposing direction —
measured as the WORST-performing bucket (29.5% hit rate vs a 34.6% base). Until
that is fixed, enabling this would feed the risk gate its least reliable signals
marked maximally confident.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# NOT `ensemble.decision`: risk-engine already consumes that queue, so
# publishing there would bypass ensemble-engine entirely AND compete with
# risk-engine for the same messages (one queue = round-robin delivery).
# The chain is  backend -> ensemble.raw -> ensemble-engine -> ensemble.decision
# -> risk-engine, so ensemble-engine keeps its MLflow gate in the path.
_EXCHANGE = "ensemble.raw"
_ENABLED_ENV = "ENSEMBLE_PUBLISH_ENABLED"

_conn: Any = None
_channel: Any = None
_warned = False


def is_enabled() -> bool:
    return os.getenv(_ENABLED_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def _rabbit_url() -> str:
    from app.config import settings
    return getattr(settings, "RABBITMQ_URL", "") or (
        f"amqp://{os.getenv('RABBITMQ_USER', 'guest')}:"
        f"{os.getenv('RABBITMQ_PASSWORD', 'guest')}@"
        f"{os.getenv('RABBITMQ_HOST', 'rabbitmq')}:"
        f"{os.getenv('RABBITMQ_PORT', '5672')}/"
    )


async def _get_channel():
    """Lazily open one robust connection. Never created unless the flag is on,
    so a disabled publisher costs nothing."""
    global _conn, _channel
    if _channel is not None and not _channel.is_closed:
        return _channel
    import aio_pika
    _conn = await aio_pika.connect_robust(_rabbit_url())
    _channel = await _conn.channel(publisher_confirms=False)
    return _channel


def build_payload(decision, symbol: str, context: Optional[dict] = None) -> dict:
    """Map the backend's EnsembleDecision onto the contract risk-engine reads
    (see risk-engine .../dto/EnsembleDecision.java). Field names are camelCase
    there; Jackson also accepts the snake_case aliases the rest of this system
    uses, so both are sent rather than guessing which binding is active."""
    ctx = context or {}
    votes = {
        s.agent_name: {
            "signal": s.action,
            "confidence": round(float(s.confidence), 4),
            "weight": round(float(s.weight), 4),
        }
        for s in getattr(decision, "agents", []) or []
    }
    conf = round(float(getattr(decision, "confidence", 0.0)), 4)
    agree = round(float(getattr(decision, "agent_agreement", 0.0) or 0.0), 4)
    return {
        "symbol": symbol,
        "exchange": ctx.get("exchange", "NSE"),
        "finalAction": getattr(decision, "action", "HOLD"),
        "final_action": getattr(decision, "action", "HOLD"),
        "weightedConfidence": conf,
        "weighted_confidence": conf,
        "agreementScore": agree,
        "agreement_score": agree,
        "uncertainty": round(1.0 - agree, 4),
        "agentVotes": votes,
        "agent_votes": votes,
        "atr": float(ctx.get("atr", 0.0) or 0.0),
        "currentPrice": float(ctx.get("price", ctx.get("current_price", 0.0)) or 0.0),
        "current_price": float(ctx.get("price", ctx.get("current_price", 0.0)) or 0.0),
        "source": "backend-ensemble",
    }


async def publish_decision(decision, symbol: str, context: Optional[dict] = None) -> bool:
    """Best-effort publish. Returns False when disabled or on any failure —
    a broker problem must never break the decision loop that produced it."""
    global _warned
    if not is_enabled():
        return False
    try:
        import aio_pika
        ch = await _get_channel()
        ex = await ch.declare_exchange(
            _EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True,
        )
        body = json.dumps(build_payload(decision, symbol, context)).encode()
        await ex.publish(aio_pika.Message(body=body), routing_key="decision")
        return True
    except Exception as exc:
        if not _warned:      # once per process: this fires per decision otherwise
            logger.warning("ensemble.decision publish failed (further "
                           "failures suppressed): %s", exc,
                           extra={"log_type": "ai_engine", "event": "publish_failed"})
            _warned = True
        return False


async def close_publisher() -> None:
    global _conn, _channel
    try:
        if _conn is not None:
            await _conn.close()
    except Exception:
        pass
    _conn = _channel = None
