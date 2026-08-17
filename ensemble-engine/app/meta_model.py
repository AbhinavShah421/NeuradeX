"""Load and run the ensemble meta-model from MLflow."""

import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

_meta_model = None
_meta_loaded = False

# MUST stay identical to AGENT_NAMES in
# model-trainer/app/trainers/meta_trainer.py — the trainer emits features in
# this exact order and this module must rebuild them the same way. The two
# cannot import from each other (separate services), so any change here needs
# the same change there, and a model trained before the change must be
# re-registered rather than loaded against the new layout.
AGENT_NAMES = [
    "technical", "pattern", "sentiment", "rl", "macro",
    "momentum", "volatility", "memory", "meanrev", "regime",
    "anomaly", "gbm", "day_structure",
]
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
            # Any non-archived version (None stage = just registered).
            # Archived MUST be filtered here: nothing was ever promoted to
            # Production/Staging, so this fallback is the branch that always
            # runs — and without the filter, archiving a bad model had no
            # effect at all. That is how v67 (trained on constant features and
            # scored on a reversed split) stayed live as a decision gate.
            all_v = [
                v for v in client.search_model_versions("name='ensemble-meta-model'")
                if (v.current_stage or "None") != "Archived"
            ]
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


def _vote_signal(raw) -> Optional[str]:
    """One agent's vote from either payload shape — nested {"signal": ...} as
    the live collector produces, or a bare "BUY" string as trade_records stores.
    Returns None when the agent is not on this panel."""
    if isinstance(raw, str):
        sig = raw.strip().upper()
        return sig if sig in _SIGNAL_ENCODE else None
    if isinstance(raw, dict):
        sig = str(raw.get("signal", "")).strip().upper()
        return sig if sig in _SIGNAL_ENCODE else None
    return None


def _build_meta_features(agent_votes: dict, ensemble_confidence: float) -> list[float]:
    """Mirrors _extract_features in meta_trainer.py exactly: per agent a signal
    encoding and a PRESENCE flag, then the three vote counts and the ensemble
    confidence. 13 * 2 + 3 + 1 = 30 features.

    Presence replaced per-agent confidence because the trainer reads
    trade_records, whose stored agent_signals is a flat {agent: "BUY"} map with
    no confidence to recover — so the old confidence slot was a constant 0.5 on
    the training side and a real number here, meaning the model was scored on
    one distribution and served another. Presence also separates "voted HOLD"
    from "not on this panel", which both encode to 0.0 in the signal slot."""
    feats: list[float] = []
    votes: list[str] = []
    for agent in AGENT_NAMES:
        sig = _vote_signal(agent_votes.get(agent))
        feats.append(_SIGNAL_ENCODE.get(sig, 0.0) if sig else 0.0)
        feats.append(1.0 if sig else 0.0)
        if sig:
            votes.append(sig)

    feats.append(float(votes.count("BUY")))
    feats.append(float(votes.count("SELL")))
    feats.append(float(votes.count("HOLD")))
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
