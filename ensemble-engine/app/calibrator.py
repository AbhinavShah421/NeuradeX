"""Load the Platt-scaling confidence calibrator from MLflow."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_calibrator = None
_calibrator_loaded = False


def load_calibrator(tracking_uri: str) -> bool:
    global _calibrator, _calibrator_loaded
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.MlflowClient()
        versions = client.get_latest_versions("confidence-calibrator", stages=["Production"])
        if not versions:
            versions = client.get_latest_versions("confidence-calibrator", stages=["Staging"])
        if not versions:
            all_v = client.search_model_versions("name='confidence-calibrator'")
            if all_v:
                versions = [sorted(all_v, key=lambda v: int(v.version), reverse=True)[0]]
        if not versions:
            logger.info("No confidence-calibrator registered yet — using raw confidence")
            return False
        uri = f"models:/confidence-calibrator/{versions[0].version}"
        _calibrator = mlflow.sklearn.load_model(uri)
        _calibrator_loaded = True
        logger.info("Loaded confidence-calibrator v%s", versions[0].version)
        return True
    except Exception as exc:
        logger.debug("Calibrator load failed (non-fatal): %s", exc)
        return False


def calibrate_confidence(raw_confidence: float, tracking_uri: str) -> float:
    """
    Map raw ensemble confidence to calibrated WIN probability.
    Returns raw_confidence unchanged if calibrator not available.
    """
    global _calibrator_loaded
    if not _calibrator_loaded:
        load_calibrator(tracking_uri)
    if _calibrator is None:
        return raw_confidence
    try:
        import numpy as np
        X = np.array([[raw_confidence]], dtype=np.float32)
        calibrated = float(_calibrator.predict_proba(X)[0][1])
        return calibrated
    except Exception as exc:
        logger.error("Calibrator predict failed: %s", exc)
        return raw_confidence
