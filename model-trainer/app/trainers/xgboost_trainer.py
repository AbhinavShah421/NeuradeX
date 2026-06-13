"""XGBoost 3-class classifier trainer for technical signals."""

import logging
from datetime import datetime

import mlflow
import mlflow.xgboost
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger(__name__)

LABEL_MAP = {0: "HOLD", 1: "BUY", 2: "SELL"}
BUY_THRESH = 1.005
SELL_THRESH = 0.995


def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    h = df["high"]
    lo = df["low"]
    v = df["volume"]

    df["ema9"] = c.ewm(span=9).mean()
    df["ema20"] = c.ewm(span=20).mean()
    df["ema50"] = c.ewm(span=50).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["rsi"] = 100 - 100 / (1 + rs)

    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    df["bb_mid"] = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_pct"] = (c - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)

    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    df["vol_ratio"] = v / (v.rolling(20).mean() + 1e-9)
    df["momentum"] = c.pct_change(5)
    df["ema_dist_20"] = (c - df["ema20"]) / (df["ema20"] + 1e-9)
    df["ema_dist_50"] = (c - df["ema50"]) / (df["ema50"] + 1e-9)

    return df


def _make_labels(df: pd.DataFrame, horizon: int = 1) -> pd.Series:
    future_ret = df["close"].shift(-horizon) / df["close"]
    labels = np.where(future_ret > BUY_THRESH, 1, np.where(future_ret < SELL_THRESH, 2, 0))
    return pd.Series(labels, index=df.index)


def _df_from_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


FEATURE_COLS = [
    "rsi", "macd", "macd_hist", "bb_pct", "atr",
    "vol_ratio", "momentum", "ema_dist_20", "ema_dist_50",
    "ema9", "ema20", "ema50",
]


async def train_xgboost(
    all_symbol_rows: dict[str, list[dict]],
    mlflow_uri: str,
    model_name: str,
    min_accuracy: float,
) -> bool:
    frames = []
    for symbol, rows in all_symbol_rows.items():
        if len(rows) < 60:
            logger.warning("Skipping %s — only %d rows", symbol, len(rows))
            continue
        df = _df_from_rows(rows)
        df = _compute_features(df)
        df["label"] = _make_labels(df)
        df = df.dropna()
        frames.append(df)

    if not frames:
        logger.error("No usable data for XGBoost training")
        return False

    # Sort by time across all symbols to preserve temporal order
    combined = pd.concat(frames, ignore_index=True)
    if "time" in combined.columns:
        combined = combined.sort_values("time").reset_index(drop=True)

    X = combined[FEATURE_COLS].values
    y = combined["label"].values.astype(int)

    params = {
        "objective": "multi:softprob",
        "num_class": 3,
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 300,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "eval_metric": "mlogloss",
        "use_label_encoder": False,
        "tree_method": "hist",
    }

    # Walk-forward time-series cross-validation (n=5 folds, no shuffle)
    tscv = TimeSeriesSplit(n_splits=5)
    fold_accuracies = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]
        m = xgb.XGBClassifier(**params)
        m.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
        fold_acc = accuracy_score(y_te, m.predict(X_te))
        fold_accuracies.append(fold_acc)
        logger.info("Walk-forward fold %d/%d accuracy: %.4f", fold, 5, fold_acc)

    mean_accuracy = float(np.mean(fold_accuracies))
    std_accuracy = float(np.std(fold_accuracies))
    logger.info(
        "Walk-forward CV: mean=%.4f std=%.4f (threshold %.4f)",
        mean_accuracy, std_accuracy, min_accuracy,
    )

    # Train final model on ALL data
    final_model = xgb.XGBClassifier(**params)
    final_model.fit(X, y, verbose=False)

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("technical-xgboost-training")

    with mlflow.start_run(run_name=f"xgb_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        mlflow.log_params(params)
        mlflow.log_metric("wf_cv_accuracy_mean", mean_accuracy)
        mlflow.log_metric("wf_cv_accuracy_std", std_accuracy)
        for i, acc in enumerate(fold_accuracies, 1):
            mlflow.log_metric(f"wf_fold_{i}_accuracy", acc)
        mlflow.log_metric("train_samples", len(X))

        if mean_accuracy >= min_accuracy:
            mlflow.xgboost.log_model(
                final_model,
                artifact_path="model",
                registered_model_name=model_name,
            )
            logger.info("Registered XGBoost model '%s' (wf_cv=%.4f)", model_name, mean_accuracy)
            return True
        else:
            logger.warning(
                "XGBoost walk-forward CV %.4f below threshold %.4f — NOT registered",
                mean_accuracy, min_accuracy,
            )
            return False
