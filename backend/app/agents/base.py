"""Base types for the multi-agent trading intelligence system."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod
from datetime import datetime


@dataclass
class AgentSignal:
    """Result from a single agent analysis."""
    agent_name: str
    action:     str    # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 – 1.0
    reasoning:  str
    indicators: dict = field(default_factory=dict)
    weight:     float = 1.0  # dynamic weight set by learning engine


@dataclass
class EnsembleDecision:
    """Final decision from the ensemble engine."""
    action:          str    # "BUY" | "SELL" | "HOLD"
    confidence:      float  # 0.0 – 1.0
    agent_agreement: float  # fraction of agents that agree with final action
    risk_score:      float  # 0.0 (safe) – 1.0 (dangerous)
    agents:          list[AgentSignal]
    reasoning:       str
    prediction_id:   Optional[str]      = None
    timestamp:       Optional[datetime] = None
    # Which vote policy produced `action`/`confidence`: "legacy" max-vote or
    # "directional" contest. Confidence scales differ between the two — gates
    # calibrated on one must not blindly apply their bands to the other.
    vote_mode:       str                = "legacy"
    # Non-empty when the ensemble REFUSED a directional action this bar (memory
    # evidence gate, anomaly/trap veto, extreme-volatility override). The refusal
    # is encoded as HOLD with LOW confidence, which slides under the session
    # gate's confident-override ceiling — so entry gates must honor this flag
    # rather than inferring intent from action+confidence.
    veto:            str                = ""


class BaseAgent(ABC):
    """Abstract base class for all trading agents."""
    name: str = "base"

    @abstractmethod
    async def analyze(
        self,
        symbol:  str,
        candles: list[dict],
        context: dict,
    ) -> AgentSignal:
        """Analyze market data and return a trading signal."""
