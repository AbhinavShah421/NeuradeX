"""
Ensemble meta-model trainer.

Trains a LightGBM binary classifier on closed trade_records to learn WHEN
the ensemble's combined vote is trustworthy (WIN=1 vs LOSS=0).

Features extracted per trade:
  - Per-agent signal direction: BUY=+1, SELL=-1, HOLD=0  (5 features)
  - Per-agent confidence                                   (5 features)
  - Ensemble: agreement_score, ensemble_confidence         (2 features)
  - Vote counts: n_buy, n_sell, n_hold                    (3 features)
  Total: 15 features

The trained model lives in MLflow as "ensemble-meta-model" and is loaded
by the ensemble-engine to produce a calibrated WIN probability as a
secondary gate alongside weighted_confidence.
"""

import json
import logging
from datetime import datetime

import asyncpg
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

AGENT_NAMES = ["technical", "pattern", "sentiment", "rl", "macro"]
MIN_SAMPLES = 50
META_MODEL_NAME = "ensemble-meta-model"
MIN_ACCURACY = 0.52

_SIGNAL_ENCODE = {"BUY": 1, "SELL": -1, "HOLD": 0}


def _extract_features(row: dict) -> list[float] | None:
    """Build feature vector from a trade_records row. Returns None if unparseable."""
    try:
        raw = row.get("agent_signals") or "{}"
        signals = json.loads(raw) if isinstance(raw, str) else raw
        if not signals:
            return None

        feats: list[float] = []
        for agent in AGENT_NAMES:
            sig = signals.get(agent, {})
            if isinstance(sig, dict):
                feats.append(float(_SIGNAL_ENCODE.get(sig.get("signal", "HOLD"), 0)))
                feats.append(float(sig.get("confidence", 0.5)))
            else:
                feats.extend([0.0, 0.5])

        votes = [signals.get(a, {}).get("signal", "HOLD") for a in AGENT_NAMES if isinstance(signals.get(a), dict)]
        feats.append(float(votes.count("BUY")))
        feats.append(float(votes.count("SELL")))
        feats.append(float(votes.count("HOLD")))
        feats.append(float(row.get("ensemble_confidence") or 0.6))

        return feats
    except Exception:
        return None


async def _load_trade_records(postgres_url: str) -> list[dict]:
    conn = await asyncpg.connect(postgres_url)
    try:
        rows = await conn.fetch(
            """
            SELECT agent_signals, ensemble_confidence, outcome
            FROM trade_records
            WHERE outcome IN ('WIN', 'LOSS')
            ORDER BY created_at DESC
            LIMIT 5000
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def train_meta_model(postgres_url: str, mlflow_uri: str) -> bool:
    records = await _load_trade_records(postgres_url)
    logger.info("Meta-trainer: loaded %d closed trade records", len(records))

    if len(records) < MIN_SAMPLES:
        logger.warning(
            "Meta-trainer: only %d samples (need %d) — skipping",
            len(records), MIN_SAMPLES,
        )
        return False

    X_rows, y_rows = [], []
    for row in records:
        feats = _extract_features(row)
        if feats is None:
            continue
        label = 1 if row["outcome"] == "WIN" else 0
        X_rows.append(feats)
        y_rows.append(label)

    if len(X_rows) < MIN_SAMPLES:
        logger.warning("Meta-trainer: only %d usable rows after parsing", len(X_rows))
        return False

    X = np.array(X_rows, dtype=np.float32)
    y = np.array(y_rows, dtype=np.int32)

    n = len(X)
    split = int(n * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    try:
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )
    except ImportError:
        logger.warning("lightgbm not available — falling back to GradientBoostingClassifier")
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )

    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    accuracy = float((y_pred == y_test).mean())
    win_rate = float(y_test.mean())

    logger.info(
        "Meta-model accuracy=%.4f  win_rate_in_test=%.4f  samples=%d",
        accuracy, win_rate, n,
    )

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("ensemble-meta-model-training")

    with mlflow.start_run(run_name=f"meta_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        mlflow.log_metric("accuracy", accuracy)
        mlflow.log_metric("win_rate", win_rate)
        mlflow.log_metric("train_samples", len(X_train))
        mlflow.log_metric("test_samples", len(X_test))

        if accuracy >= MIN_ACCURACY:
            mlflow.sklearn.log_model(
                model,
                artifact_path="model",
                registered_model_name=META_MODEL_NAME,
            )
            logger.info("Registered meta-model '%s' (accuracy=%.4f)", META_MODEL_NAME, accuracy)
            return True
        else:
            logger.warning(
                "Meta-model accuracy %.4f < threshold %.4f — NOT registered",
                accuracy, MIN_ACCURACY,
            )
            return False
