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
from .memory    import PatternMemory, MemoryAgent
from .pattern_model import PatternRecognitionModel
from .meanrev   import MeanReversionAgent
from .regime    import RegimeFilterAgent
from .anomaly   import AnomalyDetectorAgent
from .gbm_agent import GBMAgent
from .day_structure import DayStructureAgent

_engine:   EnsembleEngine | None = None
_learning: LearningSystem | None = None
_rl_agent: RLAgent        | None = None
_memory:   PatternMemory  | None = None
_pattern_model: PatternRecognitionModel | None = None


def get_memory() -> PatternMemory:
    global _memory
    if _memory is None:
        _memory = PatternMemory()
    return _memory


def get_pattern_model() -> PatternRecognitionModel:
    global _pattern_model
    if _pattern_model is None:
        _pattern_model = PatternRecognitionModel()
    return _pattern_model


def get_pattern_engine():
    """Unified pattern AI engine (model P(up) + memory win-rate → A/B/C/D grade)."""
    from .pattern_engine import get_pattern_engine as _g
    return _g()


def get_path_forecaster():
    """Monte-Carlo path forecaster (projected path/target/stop + uncertainty)."""
    from .forecast import get_path_forecaster as _g
    return _g()


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
            MemoryAgent(get_memory()),
            MeanReversionAgent(),
            RegimeFilterAgent(),
            AnomalyDetectorAgent(),
            GBMAgent(),
            DayStructureAgent(),
        ]
        _engine = EnsembleEngine(agents)
    return _engine


def get_gbm_model():
    """Gradient-Boosted P(up) model (learned non-linear pattern classifier)."""
    from .gbm_model import get_gbm_model as _g
    return _g()


def get_rl_agent() -> RLAgent:
    get_engine()  # ensures _rl_agent is set
    return _rl_agent  # type: ignore[return-value]


def get_learning() -> LearningSystem:
    global _learning
    if _learning is None:
        _learning = LearningSystem()
    return _learning


__all__ = ["get_engine", "get_learning", "get_rl_agent", "get_memory", "get_pattern_model",
           "EnsembleEngine", "LearningSystem", "PatternMemory", "MemoryAgent",
           "PatternRecognitionModel",
           "TechnicalAgent", "PatternAgent", "MomentumAgent",
           "VolatilityAgent", "SentimentAgent", "RLAgent"]
