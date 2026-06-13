"""Weighted voting aggregation across all agent signals."""

from typing import Literal
import logging

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]

DEFAULT_WEIGHTS = {
    "technical": 0.30,
    "pattern":   0.20,
    "sentiment": 0.20,
    "rl":        0.15,
    "macro":     0.15,
}

# Per-regime weight multipliers (applied before normalisation).
# Values > 1.0 boost an agent; < 1.0 dampen it.
# Rationale:
#   RISK_ON   — trending market; momentum / technicals are reliable
#   RISK_OFF  — macro stress dominates; sentiment captures fear
#   HIGH_VOL  — macro + sentiment most differentiated; patterns and RL unreliable
#   NEUTRAL   — no override; use learned DB weights as-is
_REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    "RISK_ON": {
        "technical": 1.30,
        "pattern":   1.15,
        "sentiment": 0.90,
        "rl":        1.10,
        "macro":     0.80,
    },
    "RISK_OFF": {
        "technical": 0.75,
        "pattern":   0.85,
        "sentiment": 1.30,
        "rl":        0.80,
        "macro":     1.50,
    },
    "HIGH_VOLATILITY": {
        "technical": 0.70,
        "pattern":   0.65,
        "sentiment": 1.40,
        "rl":        0.60,
        "macro":     1.60,
    },
    "NEUTRAL": {
        "technical": 1.0,
        "pattern":   1.0,
        "sentiment": 1.0,
        "rl":        1.0,
        "macro":     1.0,
    },
}


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total == 0:
        return {k: 1 / len(weights) for k in weights}
    return {k: v / total for k, v in weights.items()}


def _apply_regime_weights(
    weights: dict[str, float],
    regime: str,
) -> dict[str, float]:
    """Scale base weights by regime multipliers, then re-normalise."""
    multipliers = _REGIME_MULTIPLIERS.get(regime, _REGIME_MULTIPLIERS["NEUTRAL"])
    scaled = {k: v * multipliers.get(k, 1.0) for k, v in weights.items()}
    return _normalize_weights(scaled)


def aggregate_signals(
    agent_signals: dict[str, dict],
    weights: dict[str, float] | None = None,
    regime: str = "NEUTRAL",
) -> dict:
    """
    agent_signals: { agent_name: { signal, confidence, reasoning, indicators } }
    weights: optional override (will be normalized)
    regime: current macro regime — used to scale weights before voting
    Returns: full ensemble decision dict per REQUIREMENTS.md Section 5.
    """
    base_weights = _normalize_weights(weights or DEFAULT_WEIGHTS)
    w = _apply_regime_weights(base_weights, regime)
    logger.info("Regime=%s → effective weights: %s", regime, {k: round(v, 3) for k, v in w.items()})

    buy_score = 0.0
    sell_score = 0.0
    hold_score = 0.0
    total_weight = 0.0
    agent_votes = {}

    for agent, sig in agent_signals.items():
        agent_w = w.get(agent, 0.10)
        signal = sig.get("signal", "HOLD")
        confidence = float(sig.get("confidence", 0.5))
        weighted_conf = agent_w * confidence

        if signal == "BUY":
            buy_score += weighted_conf
        elif signal == "SELL":
            sell_score += weighted_conf
        else:
            hold_score += weighted_conf

        total_weight += agent_w
        agent_votes[agent] = {
            "signal": signal,
            "confidence": round(confidence, 3),
            "weight": round(agent_w, 3),
        }

    if total_weight == 0:
        return _no_decision(agent_votes, reason="no agents responded")

    buy_score /= total_weight
    sell_score /= total_weight
    hold_score /= total_weight

    max_score = max(buy_score, sell_score, hold_score)

    if buy_score == max_score:
        final_action = "BUY"
        weighted_confidence = buy_score
    elif sell_score == max_score:
        final_action = "SELL"
        weighted_confidence = sell_score
    else:
        final_action = "HOLD"
        weighted_confidence = hold_score

    # Compute agreement score: fraction of agents that agree with final action
    agreeing = sum(
        1 for a in agent_signals.values()
        if a.get("signal") == final_action
    )
    agreement_score = agreeing / len(agent_signals) if agent_signals else 0.0

    # Uncertainty penalty: if agents disagree significantly, downgrade confidence
    if agreement_score < 0.5:
        weighted_confidence *= 0.80
        final_action = "HOLD" if weighted_confidence < 0.55 else final_action

    # Conflict override: unanimous disagreement → force HOLD
    if len(set(v["signal"] for v in agent_votes.values())) == len(agent_votes) and len(agent_votes) > 2:
        final_action = "HOLD"
        weighted_confidence = 0.50
        logger.info("All agents disagree — forcing HOLD")

    uncertainty = round(1.0 - agreement_score, 3)

    return {
        "final_action": final_action,
        "weighted_confidence": round(weighted_confidence, 3),
        "agent_votes": agent_votes,
        "agreement_score": round(agreement_score, 3),
        "uncertainty": uncertainty,
        "regime": regime,
        "scores": {
            "buy": round(buy_score, 3),
            "sell": round(sell_score, 3),
            "hold": round(hold_score, 3),
        },
    }


def _no_decision(agent_votes: dict, reason: str = "") -> dict:
    return {
        "final_action": "HOLD",
        "weighted_confidence": 0.50,
        "agent_votes": agent_votes,
        "agreement_score": 0.0,
        "uncertainty": 1.0,
        "scores": {"buy": 0.0, "sell": 0.0, "hold": 1.0},
        "reason": reason,
    }
