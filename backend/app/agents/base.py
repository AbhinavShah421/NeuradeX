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
