"""Update ensemble agent weights based on trade outcome."""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

Outcome = Literal["WIN", "LOSS", "BREAK_EVEN"]


def compute_weight_updates(
    current_weights: dict[str, float],
    agent_signals: dict[str, dict],
    outcome: Outcome,
    action_taken: str,
    learning_rate: float = 0.05,
) -> dict[str, float]:
    """
    For each agent: if its signal matched the winning direction, increase weight.
    Rule from REQUIREMENTS.md:
      if agent_signal == outcome_direction:
        weight += lr * (1 - weight)
      else:
        weight -= lr * weight
    Then normalize so all weights sum to 1.
    """
    outcome_direction = "BUY" if outcome == "WIN" and action_taken == "BUY" else (
        "SELL" if outcome == "WIN" and action_taken == "SELL" else
        "HOLD"
    )

    updated = {}
    for agent, weight in current_weights.items():
        agent_sig = agent_signals.get(agent, {}).get("signal", "HOLD")
        if agent_sig == outcome_direction:
            new_w = weight + learning_rate * (1 - weight)
        else:
            new_w = weight - learning_rate * weight
        updated[agent] = max(0.05, min(0.60, new_w))   # clamp to [5%, 60%]

    total = sum(updated.values())
    if total > 0:
        updated = {k: round(v / total, 4) for k, v in updated.items()}

    return updated


def determine_outcome(pnl_pct: float, threshold: float = 0.001) -> Outcome:
    if pnl_pct > threshold:
        return "WIN"
    elif pnl_pct < -threshold:
        return "LOSS"
    return "BREAK_EVEN"
