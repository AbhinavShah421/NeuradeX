"""Load and run the ensemble meta-model from MLflow."""

import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

_meta_model = None
_meta_loaded = False

AGENT_NAMES = ["technical", "pattern", "sentiment", "rl", "macro"]
_SIGNAL_ENCODE = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}


def load_meta_model(tracking_uri: str) -> bool:
    global _meta_model, _meta_loaded
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.MlflowClient()
        versions = client.get_latest_versions("ensemble-meta-model", stages=["Production"])
        if not versions:
            versions = client.get_latest_versions("ensemble-meta-model", stages=["Staging"])
        if not versions:
            # Try any version (None stage = just registered)
            all_v = client.search_model_versions("name='ensemble-meta-model'")
            if all_v:
                versions = [sorted(all_v, key=lambda v: int(v.version), reverse=True)[0]]
        if not versions:
            logger.info("No ensemble-meta-model registered yet — will use weighted_confidence only")
            return False
        uri = f"models:/ensemble-meta-model/{versions[0].version}"
        _meta_model = mlflow.sklearn.load_model(uri)
        _meta_loaded = True
        logger.info("Loaded ensemble-meta-model v%s", versions[0].version)
        return True
    except Exception as exc:
        logger.debug("Meta-model load failed (non-fatal): %s", exc)
        return False


def _build_meta_features(agent_votes: dict, ensemble_confidence: float) -> list[float]:
    feats: list[float] = []
    for agent in AGENT_NAMES:
        vote = agent_votes.get(agent, {})
        feats.append(_SIGNAL_ENCODE.get(vote.get("signal", "HOLD"), 0.0))
        feats.append(float(vote.get("confidence", 0.5)))

    signals = [agent_votes.get(a, {}).get("signal", "HOLD") for a in AGENT_NAMES]
    feats.append(float(signals.count("BUY")))
    feats.append(float(signals.count("SELL")))
    feats.append(float(signals.count("HOLD")))
    feats.append(float(ensemble_confidence))
    return feats


def predict_win_probability(
    agent_votes: dict,
    ensemble_confidence: float,
    tracking_uri: str,
) -> Optional[float]:
    """
    Returns P(WIN) in [0,1] or None if meta-model is not loaded.
    Called after the ensemble vote is computed; result is stored in the decision
    payload as meta_win_probability and used as a secondary confidence gate.
    """
    global _meta_loaded
    if not _meta_loaded:
        load_meta_model(tracking_uri)
    if _meta_model is None:
        return None
    try:
        import numpy as np
        feats = _build_meta_features(agent_votes, ensemble_confidence)
        X = np.array([feats], dtype=np.float32)
        proba = _meta_model.predict_proba(X)[0]
        # Binary classifier: class 1 = WIN
        win_prob = float(proba[1]) if len(proba) == 2 else float(proba[-1])
        return win_prob
    except Exception as exc:
        logger.error("Meta-model predict error: %s", exc)
        return None
