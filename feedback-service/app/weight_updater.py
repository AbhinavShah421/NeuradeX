"""Update ensemble agent weights based on trade outcome."""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

Outcome = Literal["WIN", "LOSS", "BREAK_EVEN"]

_VALID_SIGNALS = ("BUY", "SELL", "HOLD")


def extract_signal(raw) -> str | None:
    """Read one agent's vote out of a trade_records `agent_signals` entry.

    Two shapes exist in the wild and both must work:

      flat   {"technical": "BUY", ...}                  ← what every producer
             actually writes (_derive_agent_signals in backtest_service, and
             the backend session runner). The Orders page renders this shape.
      nested {"technical": {"signal": "BUY", ...}, ...} ← the shape this module
             was originally written against.

    Reading the flat shape with the nested accessor raised
    AttributeError: 'str' object has no attribute 'get' on EVERY message. The
    consumer caught it after the trade record had already been stored, so
    trade_records filled up while agent_weights sat frozen at its seed values
    from 2026-05-30. Returns None when the agent did not vote on this trade, so
    the caller can leave its weight untouched rather than scoring it as HOLD.
    """
    if isinstance(raw, str):
        sig = raw.strip().upper()
        return sig if sig in _VALID_SIGNALS else None
    if isinstance(raw, dict):
        sig = str(raw.get("signal", "")).strip().upper()
        return sig if sig in _VALID_SIGNALS else None
    return None


def compute_weight_updates(
    current_weights: dict[str, float],
    agent_signals: dict,
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

    Agents that did not vote on this trade keep their weight unchanged. The two
    ensemble pipelines carry different agent panels (the backend session runner
    votes 12 agents with no `macro`; the ensemble-engine votes 5 including it),
    so treating an absent agent as HOLD would decay whichever panel did not
    produce the trade straight down to the floor.
    """
    outcome_direction = "BUY" if outcome == "WIN" and action_taken == "BUY" else (
        "SELL" if outcome == "WIN" and action_taken == "SELL" else
        "HOLD"
    )

    agent_signals = agent_signals or {}
    updated: dict[str, float] = {}
    scored = 0
    for agent, weight in current_weights.items():
        agent_sig = extract_signal(agent_signals.get(agent))
        if agent_sig is None:
            updated[agent] = weight          # abstained / not on this panel
            continue
        scored += 1
        if agent_sig == outcome_direction:
            new_w = weight + learning_rate * (1 - weight)
        else:
            new_w = weight - learning_rate * weight
        updated[agent] = max(0.05, min(0.60, new_w))   # clamp to [5%, 60%]

    if scored == 0:
        logger.warning(
            "No agent votes matched the weight table (payload agents=%s, table agents=%s) "
            "— weights unchanged",
            sorted(agent_signals.keys()), sorted(current_weights.keys()),
        )
        return dict(current_weights)

    total = sum(updated.values())
    if total > 0:
        updated = {k: round(v / total, 4) for k, v in updated.items()}

    return updated


def count_agent_hits(
    agent_signals: dict,
    outcome: Outcome,
    action_taken: str,
) -> dict[str, bool]:
    """Per-agent correctness for this trade, for the win_count/total_count
    columns. Only agents that actually voted appear in the result."""
    outcome_direction = "BUY" if outcome == "WIN" and action_taken == "BUY" else (
        "SELL" if outcome == "WIN" and action_taken == "SELL" else
        "HOLD"
    )
    hits: dict[str, bool] = {}
    for agent, raw in (agent_signals or {}).items():
        sig = extract_signal(raw)
        if sig is not None:
            hits[agent] = (sig == outcome_direction)
    return hits


def determine_outcome(pnl_pct: float, threshold: float = 0.001) -> Outcome:
    if pnl_pct > threshold:
        return "WIN"
    elif pnl_pct < -threshold:
        return "LOSS"
    return "BREAK_EVEN"
