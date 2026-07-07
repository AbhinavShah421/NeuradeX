"""Gradient-Boosted P(up) model — a learned, NON-LINEAR pattern classifier.

The Pattern Recognition model is a *linear* logistic regression over the 19-dim
scale-free fingerprint. This model trains a scikit-learn HistGradientBoosting
classifier on the SAME (fingerprint → realised-up/down) samples, so it can learn
feature interactions and non-linear structure a linear model can't — the
"high-capacity learned representation" upgrade, CPU-only and trainable on demand.

It persists the fitted model (pickled + base64) in Postgres so it survives
restarts, exposes predict_fp() for inference, and is consumed both as an
ensemble voice (GBMAgent) and as an extra input to the PatternEngine grade.
"""
from __future__ import annotations
import base64
import json
import pickle
import time
from typing import Optional

import numpy as np

from app.utils.elk_logger import get_logger
from .fingerprint import build_fingerprint, FINGERPRINT_DIM

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS gbm_model_state (
    id          INT PRIMARY KEY DEFAULT 1,
    blob        TEXT,
    meta        JSONB,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""


class GBMPatternModel:
    """One model per timeframe SLOT. Slot 1 = daily fingerprints (3-day
    horizon), slot 2 = intraday 1-min fingerprints (30-min horizon). A model
    must only answer questions from its own timeframe: the daily model spent
    weeks voting SELL on 97% of live 1-min bars because every intraday
    fingerprint looks dead-flat at daily scale (found 2026-07-07)."""

    def __init__(self, slot: int = 1, timeframe: str = "daily") -> None:
        self.slot = int(slot)
        self.timeframe = timeframe
        self.clf = None
        self.meta: dict = {"trained_at": None, "samples": 0, "accuracy": None,
                           "auc": None, "pos_rate": None, "timeframe": timeframe}
        self._ready = False

    async def init_db(self) -> None:
        if self._ready:
            return
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text(_DDL))
        await self.load()
        self._ready = True

    async def load(self) -> bool:
        from sqlalchemy import text
        from app.database.postgres import engine
        try:
            async with engine.begin() as conn:
                row = (await conn.execute(text(
                    "SELECT blob, meta FROM gbm_model_state WHERE id=:slot"),
                    {"slot": self.slot})).fetchone()
            if row and row[0]:
                self.clf = pickle.loads(base64.b64decode(row[0]))
                self.meta = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
                return True
        except Exception as exc:
            logger.debug("gbm load failed: %s", exc)
        return False

    async def save(self) -> None:
        from sqlalchemy import text
        from app.database.postgres import engine
        blob = base64.b64encode(pickle.dumps(self.clf)).decode("ascii")
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO gbm_model_state (id, blob, meta, updated_at)
                VALUES (:slot, :b, :m, NOW())
                ON CONFLICT (id) DO UPDATE SET blob=:b, meta=:m, updated_at=NOW()
            """), {"slot": self.slot, "b": blob, "m": json.dumps(self.meta)})

    @property
    def is_trained(self) -> bool:
        return self.clf is not None

    def predict_fp(self, fp: list[float]) -> Optional[dict]:
        if self.clf is None or fp is None or len(fp) != FINGERPRINT_DIM:
            return None
        try:
            p = float(self.clf.predict_proba(np.array(fp, dtype=float).reshape(1, -1))[0, 1])
        except Exception as exc:
            logger.debug("gbm predict failed: %s", exc)
            return None
        return {"p_up": round(p, 4), "label": "up" if p >= 0.5 else "down",
                "confidence": round(abs(p - 0.5) * 2, 4)}

    def predict_candles(self, candles: list[dict]) -> Optional[dict]:
        fp = build_fingerprint(candles)
        return self.predict_fp(fp) if fp else None

    async def fit(self, samples: list[tuple[list[float], int]], note: str = "manual") -> dict:
        """Train on (fingerprint, label) pairs with a held-out split for honest
        accuracy/AUC, then persist."""
        from sklearn.ensemble import HistGradientBoostingClassifier
        X = np.array([s[0] for s in samples], dtype=float)
        y = np.array([s[1] for s in samples], dtype=int)
        if len(set(y.tolist())) < 2 or len(y) < 200:
            return {"status": "insufficient", "samples": int(len(y))}

        # Time-ordered holdout (last 20%) — no shuffling, to avoid leakage.
        cut = int(len(X) * 0.8)
        Xtr, Xte, ytr, yte = X[:cut], X[cut:], y[:cut], y[cut:]
        clf = HistGradientBoostingClassifier(
            max_iter=200, max_depth=3, learning_rate=0.06,
            l2_regularization=1.0, min_samples_leaf=40, random_state=42)
        clf.fit(Xtr, ytr)

        acc = auc = None
        try:
            from sklearn.metrics import accuracy_score, roc_auc_score
            proba = clf.predict_proba(Xte)[:, 1]
            acc = float(accuracy_score(yte, (proba >= 0.5).astype(int)))
            if len(set(yte.tolist())) == 2:
                auc = float(roc_auc_score(yte, proba))
        except Exception:
            pass

        self.clf = clf
        self.meta = {"trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "samples": int(len(y)), "accuracy": (round(acc, 4) if acc is not None else None),
                     "auc": (round(auc, 4) if auc is not None else None),
                     "pos_rate": round(float(y.mean()), 4), "note": note,
                     "timeframe": self.timeframe}
        await self.save()
        logger.info("GBM trained: n=%d acc=%s auc=%s", len(y), acc, auc)
        return {"status": "ok", **self.meta}


_gbm: GBMPatternModel | None = None
_gbm_intraday: GBMPatternModel | None = None


def get_gbm_model() -> GBMPatternModel:
    """Daily-timeframe model (slot 1): daily fingerprints, 3-day horizon."""
    global _gbm
    if _gbm is None:
        _gbm = GBMPatternModel(slot=1, timeframe="daily")
    return _gbm


def get_gbm_intraday_model() -> GBMPatternModel:
    """Intraday model (slot 2): 1-min fingerprints from the tick store,
    30-minute horizon — the question paper sessions actually ask."""
    global _gbm_intraday
    if _gbm_intraday is None:
        _gbm_intraday = GBMPatternModel(slot=2, timeframe="intraday")
    return _gbm_intraday
