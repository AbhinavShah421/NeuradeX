"""
ML Core Module Initializer
Initializes all AI/ML models (LSTM, Transformers, XGBoost, LLM, etc.)
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Model instances
models: Dict[str, Any] = {}


async def initialize_ml_models():
    """Initialize all ML models"""
    try:
        logger.info("🤖 Initializing ML/AI models...")
        
        # Initialize LSTM model
        logger.info("Loading LSTM model...")
        models['lstm'] = await load_lstm_model()
        
        # Initialize Transformer model
        logger.info("Loading Transformer model...")
        models['transformer'] = await load_transformer_model()
        
        # Initialize XGBoost model
        logger.info("Loading XGBoost model...")
        models['xgboost'] = await load_xgboost_model()
        
        # Initialize Sentiment Analysis model
        logger.info("Loading Sentiment Analysis model...")
        models['sentiment'] = await load_sentiment_model()
        
        # Initialize LLM (Llama)
        logger.info("Loading LLM model...")
        models['llm'] = await load_llm_model()
        
        logger.info("✅ All ML models loaded successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize ML models: {str(e)}")
        raise


async def load_lstm_model():
    """Load or create LSTM model for time-series prediction"""
    try:
        # Placeholder - In production, load trained model
        logger.info("LSTM model ready (placeholder)")
        return {
            "type": "LSTM",
            "status": "loaded",
            "description": "Long Short-Term Memory network for time-series prediction",
            "input_sequence_length": 60,
            "output_length": 1
        }
    except Exception as e:
        logger.error(f"Error loading LSTM model: {str(e)}")
        raise


async def load_transformer_model():
    """Load Transformer model for pattern recognition"""
    try:
        # Placeholder - In production, load trained model
        logger.info("Transformer model ready (placeholder)")
        return {
            "type": "Transformer",
            "status": "loaded",
            "description": "Transformer model with multi-head attention",
            "num_attention_heads": 8,
            "num_layers": 4
        }
    except Exception as e:
        logger.error(f"Error loading Transformer model: {str(e)}")
        raise


async def load_xgboost_model():
    """Load XGBoost model for ensemble predictions"""
    try:
        # Placeholder - In production, load trained model
        logger.info("XGBoost model ready (placeholder)")
        return {
            "type": "XGBoost",
            "status": "loaded",
            "description": "Gradient boosting model for feature importance and predictions",
            "num_trees": 100,
            "max_depth": 7
        }
    except Exception as e:
        logger.error(f"Error loading XGBoost model: {str(e)}")
        raise


async def load_sentiment_model():
    """Load BERT-based Sentiment Analysis model"""
    try:
        # Placeholder - In production, load actual BERT model
        logger.info("Sentiment Analysis model ready (placeholder)")
        return {
            "type": "BERT-Sentiment",
            "status": "loaded",
            "description": "BERT-based sentiment analysis for news and social media",
            "model_name": "distilbert-base-uncased-finetuned-sst-2-english"
        }
    except Exception as e:
        logger.error(f"Error loading Sentiment model: {str(e)}")
        raise


async def load_llm_model():
    """Load Llama LLM for contextual analysis and reasoning"""
    try:
        # Placeholder - In production, connect to Ollama server
        logger.info("LLM (Llama) model ready (placeholder)")
        return {
            "type": "LLM-Llama2",
            "status": "loaded",
            "description": "Open-source Llama model for market analysis and explanations",
            "model_name": "llama2",
            "context_length": 4096
        }
    except Exception as e:
        logger.error(f"Error loading LLM model: {str(e)}")
        raise


def get_model(model_name: str) -> Dict[str, Any]:
    """Get a loaded model by name"""
    if model_name not in models:
        raise ValueError(f"Model '{model_name}' not found")
    return models[model_name]


async def predict_lstm(features: list) -> float:
    """Make LSTM prediction"""
    # Placeholder
    return 0.55


async def predict_transformer(features: list) -> float:
    """Make Transformer prediction"""
    # Placeholder
    return 0.58


async def predict_xgboost(features: dict) -> float:
    """Make XGBoost prediction"""
    # Placeholder
    return 0.60


# Lightweight finance-oriented sentiment lexicon. Keeps `analyze_sentiment`
# dependency-free until a real BERT model is wired in via load_sentiment_model().
_POSITIVE_WORDS = frozenset({
    "gain", "gains", "gained", "up", "rise", "rises", "rising", "rose", "rally",
    "rallied", "surge", "surged", "soar", "soared", "jump", "jumped", "boom",
    "bullish", "bull", "growth", "grow", "grew", "profit", "profits", "beat",
    "beats", "outperform", "outperformed", "strong", "strength", "upgrade",
    "upgraded", "buy", "positive", "optimistic", "record", "high", "highs",
    "win", "wins", "recovery", "recover", "rebound", "boost", "boosted",
    "good", "great", "excellent", "robust", "expand", "expansion",
})
_NEGATIVE_WORDS = frozenset({
    "loss", "losses", "lost", "down", "fall", "falls", "falling", "fell",
    "drop", "drops", "dropped", "plunge", "plunged", "slump", "slumped",
    "crash", "crashed", "bearish", "bear", "decline", "declined", "weak",
    "weakness", "miss", "missed", "downgrade", "downgraded", "sell",
    "negative", "pessimistic", "low", "lows", "loss-making", "risk", "risks",
    "fear", "fears", "concern", "concerns", "warn", "warning", "warned",
    "cut", "cuts", "slowdown", "recession", "default", "bankrupt", "bad",
    "poor", "weakening", "underperform", "underperformed", "tumble", "tumbled",
})
_NEGATION_WORDS = frozenset({"not", "no", "never", "without", "n't", "neither", "nor"})


async def analyze_sentiment(text: str) -> float:
    """Analyze sentiment of text, returning a score in [-1.0, 1.0].

    Lexicon-based scorer with simple negation handling: a positive/negative
    word preceded (within two tokens) by a negation flips its polarity.
    Returns 0.0 for empty or neutral text.
    """
    if not text or not text.strip():
        return 0.0

    import re

    tokens = re.findall(r"[a-zA-Z']+", text.lower())
    if not tokens:
        return 0.0

    score = 0
    for i, token in enumerate(tokens):
        if token in _POSITIVE_WORDS:
            polarity = 1
        elif token in _NEGATIVE_WORDS:
            polarity = -1
        else:
            continue
        # Flip polarity if a negation appears in the preceding two tokens.
        if any(prev in _NEGATION_WORDS for prev in tokens[max(0, i - 2):i]):
            polarity = -polarity
        score += polarity

    if score == 0:
        return 0.0

    # Normalize by token count so longer texts aren't unboundedly large,
    # then clamp to the [-1, 1] contract.
    normalized = score / (len(tokens) ** 0.5)
    return max(-1.0, min(1.0, round(normalized, 4)))


async def generate_analysis(prompt: str) -> str:
    """Generate analysis using LLM"""
    # Placeholder - In production, call Ollama API
    return "Market analysis generated by Llama model..."
