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

# Union of both ensemble panels. The backend session runner votes 12 agents
# (no `macro`); the ensemble-engine votes 5 (including it). The old list held
# only the ensemble-engine's five, so a session trade contributed nothing.
AGENT_NAMES = [
    "technical", "pattern", "sentiment", "rl", "macro",
    "momentum", "volatility", "memory", "meanrev", "regime",
    "anomaly", "gbm", "day_structure",
]
MIN_SAMPLES = 50
META_MODEL_NAME = "ensemble-meta-model"

# AUC is the gate, because the ensemble-engine consumes this model's OUTPUT AS A
# PROBABILITY (predict_win_probability) and thresholds it downstream — ranking
# quality is what it needs, not argmax accuracy at 0.5.
#
# The old gate was raw accuracy >= 0.52, which is meaningless on a ~33/67
# imbalanced label: always predicting LOSS scores 0.669, and that is exactly
# what the last registered version scored. A constant predictor passed and was
# then loaded as a live secondary gate. It would score AUC 0.5 here and be
# rejected.
#
# Accuracy lift is logged but deliberately NOT gated on: a model with genuine
# ranking skill (AUC 0.75) can still sit near the majority-class rate on
# accuracy, and blocking it on that basis discards the signal the gate exists
# to capture.
MIN_AUC = 0.60

_SIGNAL_ENCODE = {"BUY": 1, "SELL": -1, "HOLD": 0}


def _agent_vote(raw) -> str | None:
    """Read one agent's vote. Producers write a flat {"technical": "BUY"} map;
    this module previously assumed a nested {"signal": ...} dict, so isinstance
    sent every real row down the else-branch and emitted a constant feature
    vector. Only `ensemble_confidence` varied, which is why the model could do
    no better than the majority class."""
    if isinstance(raw, str):
        sig = raw.strip().upper()
        return sig if sig in _SIGNAL_ENCODE else None
    if isinstance(raw, dict):
        sig = str(raw.get("signal", "")).strip().upper()
        return sig if sig in _SIGNAL_ENCODE else None
    return None


def _extract_features(row: dict) -> list[float] | None:
    """Build feature vector from a trade_records row. Returns None if unparseable."""
    try:
        raw = row.get("agent_signals") or "{}"
        signals = json.loads(raw) if isinstance(raw, str) else raw
        if not signals:
            return None

        votes: list[str] = []
        feats: list[float] = []
        for agent in AGENT_NAMES:
            vote = _agent_vote(signals.get(agent))
            feats.append(float(_SIGNAL_ENCODE.get(vote, 0)) if vote else 0.0)
            # Presence flag: distinguishes "voted HOLD" from "not on this panel",
            # which both encode to 0 in the line above.
            feats.append(1.0 if vote else 0.0)
            if vote:
                votes.append(vote)

        if not votes:
            return None

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
        # Most recent 5000, returned OLDEST-FIRST. train_meta_model splits
        # X[:80%] into train and X[80%:] into test, so a DESC ordering here
        # trained the model on the newest trades and evaluated it on the
        # oldest — fitting the future to predict the past, which inflates
        # every reported metric. The inner LIMIT still selects recent history;
        # the outer sort makes the split chronological.
        rows = await conn.fetch(
            """
            SELECT agent_signals, ensemble_confidence, outcome
            FROM (
                SELECT agent_signals, ensemble_confidence, outcome, created_at
                FROM trade_records
                WHERE outcome IN ('WIN', 'LOSS')
                ORDER BY created_at DESC
                LIMIT 5000
            ) recent
            ORDER BY created_at ASC
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

    # The bar a constant predictor clears for free.
    baseline = max(win_rate, 1.0 - win_rate)
    lift = accuracy - baseline

    try:
        from sklearn.metrics import roc_auc_score
        y_prob = model.predict_proba(X_test)[:, 1]
        auc = float(roc_auc_score(y_test, y_prob)) if len(set(y_test.tolist())) > 1 else 0.5
    except Exception:
        auc = 0.5

    logger.info(
        "Meta-model accuracy=%.4f  baseline=%.4f  lift=%+.4f  auc=%.4f  "
        "win_rate_in_test=%.4f  samples=%d",
        accuracy, baseline, lift, auc, win_rate, n,
    )

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("ensemble-meta-model-training")

    with mlflow.start_run(run_name=f"meta_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        mlflow.log_metric("accuracy", accuracy)
        mlflow.log_metric("baseline", baseline)
        mlflow.log_metric("lift", lift)
        mlflow.log_metric("auc", auc)
        mlflow.log_metric("win_rate", win_rate)
        mlflow.log_metric("train_samples", len(X_train))
        mlflow.log_metric("test_samples", len(X_test))

        if auc >= MIN_AUC:
            mlflow.sklearn.log_model(
                model,
                artifact_path="model",
                registered_model_name=META_MODEL_NAME,
            )
            logger.info(
                "Registered meta-model '%s' (auc=%.4f, accuracy=%.4f vs baseline %.4f, lift=%+.4f)",
                META_MODEL_NAME, auc, accuracy, baseline, lift,
            )
            return True
        else:
            logger.warning(
                "Meta-model NOT registered — auc %.4f below threshold %.4f "
                "(accuracy %.4f vs majority-class %.4f, lift %+.4f)",
                auc, MIN_AUC, accuracy, baseline, lift,
            )
            return False
