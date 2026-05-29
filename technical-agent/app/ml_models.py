"""Load XGBoost model from MLflow registry; fall back to rule-based signal."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_model = None          # XGBoost booster
_model_loaded = False


def _try_load_from_mlflow(tracking_uri: str) -> bool:
    global _model, _model_loaded
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.MlflowClient()
        versions = client.get_latest_versions("technical-xgboost", stages=["Production"])
        if not versions:
            versions = client.get_latest_versions("technical-xgboost", stages=["Staging"])
        if not versions:
            return False
        model_uri = f"models:/technical-xgboost/{versions[0].version}"
        _model = mlflow.xgboost.load_model(model_uri)
        _model_loaded = True
        logger.info("Loaded technical-xgboost v%s from MLflow", versions[0].version)
        return True
    except Exception as exc:
        logger.debug("MLflow model load failed (will use rule-based): %s", exc)
        return False


def get_model(tracking_uri: str):
    global _model, _model_loaded
    if not _model_loaded:
        _try_load_from_mlflow(tracking_uri)
    return _model


def predict_xgboost(model, feature_vector: list[float]) -> tuple[str, float]:
    """Returns (signal, confidence). signal ∈ {'BUY','SELL','HOLD'}."""
    try:
        import numpy as np
        import xgboost as xgb
        dmatrix = xgb.DMatrix(np.array([feature_vector]))
        probs = model.predict(dmatrix)[0]
        # Assume 3-class output: [HOLD, BUY, SELL]
        classes = ["HOLD", "BUY", "SELL"]
        if len(probs) == 3:
            idx = int(probs.argmax())
            return classes[idx], float(probs[idx])
        # Binary: probability of BUY
        p = float(probs)
        if p > 0.6:
            return "BUY", p
        elif p < 0.4:
            return "SELL", 1.0 - p
        return "HOLD", 1.0 - abs(p - 0.5) * 2
    except Exception as exc:
        logger.error("XGBoost predict failed: %s", exc)
        return "HOLD", 0.5
