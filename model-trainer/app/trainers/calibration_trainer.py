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

    # Chronological holdout. Previously this fit on X and then scored Brier on
    # THE SAME X, comparing it to a constant-mean baseline. A 2-parameter
    # logistic regression minimising log-loss essentially always wins that
    # comparison in-sample, so `brier < baseline_brier` was very nearly a
    # tautology and the trainer registered a new version on every single run
    # (71 of them) whether or not the confidence score carried any signal.
    # Rows arrive ordered by created_at ASC, so this split is train-on-past /
    # test-on-future, and both the model and the baseline are now scored on
    # data neither of them saw.
    split = int(n * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    if len(X_test) < 10 or len(set(y_test.tolist())) < 2:
        logger.warning(
            "Calibration trainer: holdout too small or single-class "
            "(%d rows) — NOT registered", len(X_test),
        )
        return False

    calibrator = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    calibrator.fit(X_train, y_train)

    calibrated_probs = calibrator.predict_proba(X_test)[:, 1]
    brier = brier_score_loss(y_test, calibrated_probs)
    logloss = log_loss(y_test, calibrated_probs, labels=[0, 1])

    # Naive baseline: predict the TRAINING win rate on the holdout. Using the
    # holdout's own mean would give the baseline information the model does
    # not have, flattering the model by comparison.
    baseline_brier = brier_score_loss(y_test, np.full(len(y_test), y_train.mean()))

    logger.info(
        "Calibrator: Brier=%.4f (baseline=%.4f) LogLoss=%.4f "
        "train=%d test=%d",
        brier, baseline_brier, logloss, len(X_train), len(X_test),
    )

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("confidence-calibration-training")

    with mlflow.start_run(run_name=f"calib_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        mlflow.log_metric("brier_score", brier)
        mlflow.log_metric("baseline_brier", baseline_brier)
        mlflow.log_metric("log_loss", logloss)
        mlflow.log_metric("samples", n)
        mlflow.log_metric("train_samples", len(X_train))
        mlflow.log_metric("test_samples", len(X_test))
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
