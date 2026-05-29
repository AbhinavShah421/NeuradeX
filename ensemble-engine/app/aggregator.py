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


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total == 0:
        return {k: 1 / len(weights) for k in weights}
    return {k: v / total for k, v in weights.items()}


def aggregate_signals(
    agent_signals: dict[str, dict],
    weights: dict[str, float] | None = None,
) -> dict:
    """
    agent_signals: { agent_name: { signal, confidence, reasoning, indicators } }
    weights: optional override (will be normalized)
    Returns: full ensemble decision dict per REQUIREMENTS.md Section 5.
    """
    w = _normalize_weights(weights or DEFAULT_WEIGHTS)

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
