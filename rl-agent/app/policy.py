"""Load RL policy from MLflow or use momentum heuristic as fallback."""

import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

_model = None
_model_loaded = False


def _try_load_mlflow(tracking_uri: str, model_name: str) -> bool:
    global _model, _model_loaded
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.MlflowClient()
        versions = client.get_latest_versions(model_name, stages=["Production"])
        if not versions:
            versions = client.get_latest_versions(model_name, stages=["Staging"])
        if not versions:
            return False
        model_uri = f"models:/{model_name}/{versions[0].version}"
        _model = mlflow.pyfunc.load_model(model_uri)
        _model_loaded = True
        logger.info("RL policy loaded from MLflow: %s v%s", model_name, versions[0].version)
        return True
    except Exception as exc:
        logger.debug("RL MLflow load failed (momentum fallback): %s", exc)
        return False


def get_policy(tracking_uri: str, model_name: str):
    global _model, _model_loaded
    if not _model_loaded:
        _try_load_mlflow(tracking_uri, model_name)
    return _model


def predict_action(policy, obs: np.ndarray, indicators: dict) -> tuple[str, float]:
    """Returns (signal, confidence). Falls back to momentum heuristic."""
    if policy is not None:
        try:
            action, _ = policy.predict(obs, deterministic=True)
            action = int(action)
            actions = {0: "HOLD", 1: "BUY", 2: "SELL"}
            signal = actions.get(action, "HOLD")
            return signal, 0.65
        except Exception as exc:
            logger.warning("RL predict error: %s", exc)

    # Momentum heuristic fallback
    rsi = indicators.get("rsi_14", 50)
    macd_hist = indicators.get("macd_hist", 0)
    vol_ratio = indicators.get("volume_ratio", 1.0)

    momentum_score = 0
    if rsi < 35:
        momentum_score += 2
    elif rsi > 65:
        momentum_score -= 2
    if macd_hist > 0:
        momentum_score += 1
    elif macd_hist < 0:
        momentum_score -= 1
    if vol_ratio > 1.5:
        momentum_score = int(momentum_score * 1.2)

    if momentum_score >= 2:
        return "BUY", 0.60
    elif momentum_score <= -2:
        return "SELL", 0.60
    return "HOLD", 0.50
