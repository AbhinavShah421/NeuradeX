"""
Confidence calibration trainer using Platt scaling (logistic regression).

Problem: the ensemble's raw `weighted_confidence` is not calibrated — a reported
confidence of 0.72 does NOT mean a 72% win probability. Platt scaling trains a
2-parameter sigmoid on held-out predictions to map raw scores to true win%.

Input  (from trade_records):
  - ensemble_confidence  (the score the ensemble reported)
  - outcome: WIN → 1, LOSS → 0

Output (registered to MLflow as "confidence-calibrator"):
  - sklearn LogisticRegression with 1 input feature
  - Call calibrator.predict_proba([[raw_conf]])[0][1] to get calibrated WIN%

The calibrator is loaded by the ensemble-engine and applied to weighted_confidence
BEFORE the decision is published, replacing the raw score with a calibrated one.
"""

import logging
from datetime import datetime

import asyncpg
import mlflow
import mlflow.sklearn
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

logger = logging.getLogger(__name__)

CALIBRATOR_NAME = "confidence-calibrator"
MIN_SAMPLES = 30


async def _load_calibration_data(postgres_url: str) -> tuple[np.ndarray, np.ndarray]:
    conn = await asyncpg.connect(postgres_url)
    try:
        rows = await conn.fetch(
            """
            SELECT ensemble_confidence, outcome
            FROM trade_records
            WHERE outcome IN ('WIN', 'LOSS')
              AND ensemble_confidence IS NOT NULL
            ORDER BY created_at ASC
            """
        )
        X = np.array([[float(r["ensemble_confidence"])] for r in rows], dtype=np.float32)
        y = np.array([1 if r["outcome"] == "WIN" else 0 for r in rows], dtype=np.int32)
        return X, y
    finally:
        await conn.close()


async def train_calibrator(postgres_url: str, mlflow_uri: str) -> bool:
    X, y = await _load_calibration_data(postgres_url)
    n = len(X)
    logger.info("Calibration trainer: %d samples (%.1f%% win rate)", n, y.mean() * 100 if n else 0)

    if n < MIN_SAMPLES:
        logger.warning("Calibration trainer: only %d samples — need %d, skipping", n, MIN_SAMPLES)
        return False

    # Platt scaling: fit a logistic regression on raw confidence → win label
    calibrator = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    calibrator.fit(X, y)

    calibrated_probs = calibrator.predict_proba(X)[:, 1]
    brier = brier_score_loss(y, calibrated_probs)
    logloss = log_loss(y, calibrated_probs)

    # Naive baseline: just predict the mean win rate everywhere
    baseline_brier = brier_score_loss(y, np.full(n, y.mean()))

    logger.info(
        "Calibrator: Brier=%.4f (baseline=%.4f) LogLoss=%.4f samples=%d",
        brier, baseline_brier, logloss, n,
    )

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("confidence-calibration-training")

    with mlflow.start_run(run_name=f"calib_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        mlflow.log_metric("brier_score", brier)
        mlflow.log_metric("baseline_brier", baseline_brier)
        mlflow.log_metric("log_loss", logloss)
        mlflow.log_metric("samples", n)
        mlflow.log_metric("win_rate", float(y.mean()))

        # Register if it beats the naive baseline
        if brier < baseline_brier:
            mlflow.sklearn.log_model(
                calibrator,
                artifact_path="model",
                registered_model_name=CALIBRATOR_NAME,
            )
            logger.info(
                "Registered '%s' (Brier=%.4f < baseline=%.4f)",
                CALIBRATOR_NAME, brier, baseline_brier,
            )
            return True
        else:
            logger.warning(
                "Calibrator Brier %.4f >= baseline %.4f — NOT registered (insufficient signal)",
                brier, baseline_brier,
            )
            return False
