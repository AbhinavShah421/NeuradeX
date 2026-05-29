"""AI Engine — singleton factory for ensemble engine + learning system."""
from __future__ import annotations
from .technical import TechnicalAgent
from .pattern   import PatternAgent
from .momentum  import MomentumAgent
from .volatility import VolatilityAgent
from .sentiment import SentimentAgent
from .rl_agent  import RLAgent
from .ensemble  import EnsembleEngine
from .learning  import LearningSystem

_engine:   EnsembleEngine | None = None
_learning: LearningSystem | None = None
_rl_agent: RLAgent        | None = None


def get_engine() -> EnsembleEngine:
    global _engine, _rl_agent
    if _engine is None:
        _rl_agent = RLAgent()
        agents = [
            TechnicalAgent(),
            PatternAgent(),
            MomentumAgent(),
            VolatilityAgent(),
            SentimentAgent(),
            _rl_agent,
        ]
        _engine = EnsembleEngine(agents)
    return _engine


def get_rl_agent() -> RLAgent:
    get_engine()  # ensures _rl_agent is set
    return _rl_agent  # type: ignore[return-value]


def get_learning() -> LearningSystem:
    global _learning
    if _learning is None:
        _learning = LearningSystem()
    return _learning


__all__ = ["get_engine", "get_learning", "get_rl_agent",
           "EnsembleEngine", "LearningSystem",
           "TechnicalAgent", "PatternAgent", "MomentumAgent",
           "VolatilityAgent", "SentimentAgent", "RLAgent"]
