"""Ensemble Decision Engine — runs all agents in parallel, weighted voting."""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime
from .base import AgentSignal, BaseAgent, EnsembleDecision
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

DEFAULT_WEIGHTS: dict[str, float] = {
    "technical":  1.0,
    "pattern":    1.0,
    "momentum":   1.0,
    "volatility": 1.0,
    "sentiment":  1.0,
    "rl":         0.8,  # lower until RL proves itself
    "memory":     1.3,  # historical precedent carries extra weight in the vote
}

# Evidence gate: a non-HOLD decision is only allowed to fire if the Pattern
# Memory bank has enough similar past cases AND they won often enough.
_MEM_MIN_SAMPLES   = 8
_MEM_GATE_WINRATE  = 0.50   # below this, veto the trade → HOLD (abstain)
_MEM_STRONG_WINRATE = 0.65  # above this, actively boost confidence


class EnsembleEngine:
    def __init__(self, agents: list[BaseAgent]) -> None:
        self.agents   = agents
        self._weights = dict(DEFAULT_WEIGHTS)

    def update_weights(self, weights: dict[str, float]) -> None:
        self._weights.update(weights)

    async def decide(
        self,
        symbol:  str,
        candles: list[dict],
        context: dict,
    ) -> EnsembleDecision:
        """Run all agents in parallel, combine with weighted voting."""

        raw = await asyncio.gather(
            *[a.analyze(symbol, candles, context) for a in self.agents],
            return_exceptions=True,
        )

        signals: list[AgentSignal] = []
        for result, agent in zip(raw, self.agents):
            if isinstance(result, Exception):
                logger.warning("Agent %s error: %s", agent.name, result)
                signals.append(AgentSignal(
                    agent_name=agent.name, action="HOLD",
                    confidence=0.30, reasoning=f"Error: {result}",
                ))
            else:
                result.weight = self._weights.get(result.agent_name, 1.0)
                signals.append(result)

        # Weighted vote
        vote: dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        for s in signals:
            vote[s.action] += s.confidence * s.weight

        total    = sum(vote.values()) or 1.0
        vote_pct = {k: v / total for k, v in vote.items()}
        action   = max(vote, key=lambda k: vote[k])
        confidence = 0.30 + vote_pct[action] * 0.65

        # Agreement score
        agreers   = sum(1 for s in signals if s.action == action)
        agreement = agreers / len(signals) if signals else 0.0

        # Risk score from volatility agent
        risk_score = 0.50
        for s in signals:
            if s.agent_name == "volatility":
                risk_score = float(s.indicators.get("risk_score", 0.50))
                break

        # ── Pattern-memory evidence gate ────────────────────────────────────────
        # This is the lever that pushes win-rate up: only act when similar past
        # situations actually paid off; otherwise abstain (HOLD). When evidence is
        # strong, boost confidence; when memory is empty (cold start), stay neutral.
        mem = next((s for s in signals if s.agent_name == "memory"), None)
        memory_note = ""
        if mem is not None:
            mi = mem.indicators or {}
            samples = int(mi.get("sample_count", 0))
            if samples >= _MEM_MIN_SAMPLES and action in ("BUY", "SELL"):
                wr = mi.get(f"wr_{action}")
                if wr is None:
                    # The bank has cases but none for this action nearby → weak edge
                    action = "HOLD"
                    confidence = 0.55
                    memory_note = f"memory veto: no similar {action} precedent"
                elif wr < _MEM_GATE_WINRATE:
                    action = "HOLD"
                    confidence = 0.55
                    memory_note = f"memory veto: similar setups won only {wr:.0%}"
                else:
                    # Scale confidence by how well this action did historically
                    boost = 0.85 + 0.45 * max(0.0, wr - 0.5)
                    confidence = min(0.95, confidence * boost)
                    if wr >= _MEM_STRONG_WINRATE:
                        memory_note = f"memory confirms: {wr:.0%} historical win-rate"
                    else:
                        memory_note = f"memory ok: {wr:.0%} historical win-rate"

        # Override to HOLD in extreme volatility (risk beats everything)
        if risk_score > 0.80 and action != "HOLD":
            action     = "HOLD"
            confidence = 0.60
            reasoning  = f"High volatility risk ({risk_score:.2f}) → forced HOLD"
        else:
            top = sorted(signals, key=lambda s: s.confidence * s.weight, reverse=True)[:2]
            reasoning = " | ".join(
                f"{s.agent_name}: {s.action} {s.confidence:.0%}" for s in top
            )
            if memory_note:
                reasoning = f"{reasoning}  ·  {memory_note}"

        confidence = round(confidence, 3)
        pred_id = str(uuid.uuid4())

        logger.info("Ensemble decision",
                    extra={"log_type": "ai_engine", "event": "ensemble_decision",
                           "symbol": symbol, "action": action,
                           "confidence": round(confidence, 3),
                           "agreement": round(agreement, 3),
                           "risk_score": round(risk_score, 3),
                           "prediction_id": pred_id,
                           "vote": {k: round(v, 3) for k, v in vote_pct.items()}})

        return EnsembleDecision(
            action          = action,
            confidence      = round(confidence, 3),
            agent_agreement = round(agreement, 3),
            risk_score      = round(risk_score, 3),
            agents          = signals,
            reasoning       = reasoning,
            prediction_id   = pred_id,
            timestamp       = datetime.now(),
        )
