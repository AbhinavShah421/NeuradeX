"""Pattern Recognition Model — a dedicated, continuously-learning classifier that
maps a price *pattern* (and nothing else) to the probability the next move is up.

Unlike the ensemble agents (which blend technicals, news, RL, etc.), this model
considers ONLY the pattern: the 19-dim scale-free fingerprint of a candle window
(see fingerprint.py). It is an online logistic-regression learner — every labelled
example nudges the weights, so it keeps getting smarter as backtesting feeds it
more (pattern → realised outcome) pairs. Its weights and a learning curve are
persisted in Postgres so progress survives restarts and can be visualised.

This is intentionally a *separate* model from the Pattern Memory bank (k-NN). The
memory bank recalls specific past cases; this model generalises a smooth decision
surface over pattern space and reports a single, improving accuracy.
"""
from __future__ import annotations
import json
import time
from typing import Optional

import numpy as np

from app.utils.elk_logger import get_logger
from .fingerprint import build_fingerprint, FINGERPRINT_DIM

logger = get_logger(__name__)

# Online-learning hyper-parameters
_LR        = 0.05      # SGD step size
_L2        = 1e-4      # weight decay (regularisation)
_EMA_ALPHA = 0.02      # how fast the rolling accuracy tracks recent performance
_LABEL_UP  = 0.30      # forward return ≥ this (%) labels a pattern "up"

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_model_state (
    id            INT PRIMARY KEY DEFAULT 1,
    weights       JSONB,
    bias          DOUBLE PRECISION DEFAULT 0,
    n_samples     BIGINT DEFAULT 0,
    n_correct     BIGINT DEFAULT 0,
    ema_accuracy  DOUBLE PRECISION,
    version       INT DEFAULT 1,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""
_CURVE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_model_curve (
    id             SERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ DEFAULT NOW(),
    n_samples      BIGINT,
    ema_accuracy   DOUBLE PRECISION,
    batch_accuracy DOUBLE PRECISION,
    batch_size     INT,
    note           TEXT
);
"""


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + np.exp(-z))
    e = np.exp(z)
    return e / (1.0 + e)


class PatternRecognitionModel:
    def __init__(self) -> None:
        self.w = np.zeros(FINGERPRINT_DIM, dtype=np.float64)
        self.b = 0.0
        self.n_samples = 0
        self.n_correct = 0
        self.ema_accuracy: Optional[float] = None
        self._ready = False
        self._dirty = False
        self._last_save = 0.0

    # ── persistence ────────────────────────────────────────────────────────────
    async def init_db(self) -> None:
        if self._ready:
            return
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text(_STATE_DDL))
            await conn.execute(text(_CURVE_DDL))
            row = (await conn.execute(text(
                "SELECT weights, bias, n_samples, n_correct, ema_accuracy FROM pattern_model_state WHERE id=1"
            ))).fetchone()
        if row and row[0] is not None:
            try:
                wv = row[0] if isinstance(row[0], list) else json.loads(row[0])
                if len(wv) == FINGERPRINT_DIM:
                    self.w = np.array(wv, dtype=np.float64)
                self.b = float(row[1] or 0.0)
                self.n_samples = int(row[2] or 0)
                self.n_correct = int(row[3] or 0)
                self.ema_accuracy = float(row[4]) if row[4] is not None else None
            except Exception as exc:
                logger.warning("pattern model load failed: %s", exc)
        self._ready = True

    async def save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO pattern_model_state (id, weights, bias, n_samples, n_correct, ema_accuracy, updated_at)
                VALUES (1, CAST(:w AS jsonb), :b, :n, :c, :e, NOW())
                ON CONFLICT (id) DO UPDATE SET
                  weights=CAST(:w AS jsonb), bias=:b, n_samples=:n, n_correct=:c,
                  ema_accuracy=:e, updated_at=NOW()
            """), {"w": json.dumps([round(float(x), 6) for x in self.w]), "b": float(self.b),
                   "n": int(self.n_samples), "c": int(self.n_correct),
                   "e": (float(self.ema_accuracy) if self.ema_accuracy is not None else None)})
        self._dirty = False
        self._last_save = time.time()

    async def _snapshot_curve(self, batch_acc: float, batch_size: int, note: str) -> None:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO pattern_model_curve (n_samples, ema_accuracy, batch_accuracy, batch_size, note)
                VALUES (:n, :e, :ba, :bs, :note)
            """), {"n": int(self.n_samples),
                   "e": (float(self.ema_accuracy) if self.ema_accuracy is not None else None),
                   "ba": round(float(batch_acc), 4), "bs": int(batch_size), "note": note})

    # ── inference ────────────────────────────────────────────────────────────────
    def _p(self, x: np.ndarray) -> float:
        return _sigmoid(float(np.dot(self.w, x)) + self.b)

    def predict_fp(self, fp: list[float]) -> Optional[dict]:
        if not fp or len(fp) != FINGERPRINT_DIM:
            return None
        p = self._p(np.array(fp, dtype=np.float64))
        return {"p_up": round(p, 4), "label": "up" if p >= 0.5 else "down",
                "confidence": round(abs(p - 0.5) * 2, 4)}

    def predict_candles(self, candles: list[dict]) -> Optional[dict]:
        fp = build_fingerprint(candles)
        return self.predict_fp(fp) if fp else None

    # ── learning ─────────────────────────────────────────────────────────────────
    def _step(self, x: np.ndarray, y: int) -> bool:
        """One SGD step on logloss; returns whether the pre-update call was correct."""
        p = self._p(x)
        correct = (p >= 0.5) == (y == 1)
        g = p - y
        self.w -= _LR * (g * x + _L2 * self.w)
        self.b -= _LR * g
        self.n_samples += 1
        self.n_correct += 1 if correct else 0
        self.ema_accuracy = (1.0 if correct else 0.0) if self.ema_accuracy is None \
            else self.ema_accuracy * (1 - _EMA_ALPHA) + (1.0 if correct else 0.0) * _EMA_ALPHA
        return correct

    async def learn_one(self, fp: list[float], label: int) -> None:
        await self.init_db()
        if not fp or len(fp) != FINGERPRINT_DIM:
            return
        self._step(np.array(fp, dtype=np.float64), 1 if label else 0)
        self._dirty = True
        if time.time() - self._last_save > 20:     # debounce live writes
            await self.save()

    async def learn_batch(self, samples: list[tuple[list[float], int]], epochs: int = 3,
                          note: str = "train") -> dict:
        """Train on a batch of (fingerprint, label) pairs over several shuffled
        epochs. Records a learning-curve snapshot (pre-training accuracy on this
        batch — an honest held-out-ish read since the weights haven't seen it yet)."""
        await self.init_db()
        X = [(np.array(fp, dtype=np.float64), 1 if y else 0)
             for fp, y in samples if fp and len(fp) == FINGERPRINT_DIM]
        if not X:
            return {"status": "empty"}

        # Pre-training accuracy on this fresh batch ≈ generalisation signal.
        pre_correct = sum(1 for x, y in X if (self._p(x) >= 0.5) == (y == 1))
        batch_acc = pre_correct / len(X)

        rng = np.random.default_rng()
        for _ in range(max(1, epochs)):
            order = rng.permutation(len(X))
            for i in order:
                x, y = X[i]
                self._step(x, y)
        self._dirty = True
        await self.save(force=True)
        await self._snapshot_curve(batch_acc, len(X), note)
        logger.info("pattern model trained: +%d samples, batch_acc %.1f%%, ema %.1f%%",
                    len(X), batch_acc * 100, (self.ema_accuracy or 0) * 100)
        return {"status": "ok", "added": len(X), "batch_accuracy": round(batch_acc, 4),
                "ema_accuracy": round(self.ema_accuracy or 0.0, 4), "n_samples": self.n_samples}

    # ── reporting ─────────────────────────────────────────────────────────────────
    async def stats(self) -> dict:
        await self.init_db()
        lifetime = (self.n_correct / self.n_samples) if self.n_samples else None
        return {
            "n_samples": self.n_samples,
            "lifetime_accuracy": round(lifetime, 4) if lifetime is not None else None,
            "recent_accuracy": round(self.ema_accuracy, 4) if self.ema_accuracy is not None else None,
            "dim": FINGERPRINT_DIM,
            "trained": self.n_samples > 0,
        }

    async def curve(self, limit: int = 200) -> list[dict]:
        await self.init_db()
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT ts, n_samples, ema_accuracy, batch_accuracy, batch_size, note "
                "FROM pattern_model_curve ORDER BY id ASC"
            ))).fetchall()
        pts = [{
            "ts": r[0].isoformat() if r[0] else None,
            "n_samples": int(r[1] or 0),
            "recent_accuracy": round(float(r[2]), 4) if r[2] is not None else None,
            "batch_accuracy": round(float(r[3]), 4) if r[3] is not None else None,
            "batch_size": int(r[4] or 0), "note": r[5],
        } for r in rows]
        return pts[-limit:]


# ── Pattern-only training from backtest history ───────────────────────────────

import asyncio
from datetime import datetime, timedelta

_train_lock = asyncio.Lock()
_last_train: dict | None = None


def get_last_train() -> dict | None:
    return _last_train


def is_training() -> bool:
    return _train_lock.locked()


async def train_pattern_model(symbols: list[str] | None = None, lookback_days: int = 365,
                              horizon: int = 3, stride: int = 1, trigger: str = "manual") -> dict:
    """Backtest-as-pattern-trainer: walk historical candles, and for each window
    build the pattern fingerprint (no lookahead) and label it by the realised
    forward return `horizon` bars later. ONLY the pattern and its outcome are
    used — no indicators/news/RL — then feed the pattern model. This is what makes
    the recogniser keep getting smarter from backtesting."""
    global _last_train
    if _train_lock.locked():
        return {"status": "already_running"}

    async with _train_lock:
        from app.api.backtest import _fetch_candles
        from app.api.agent import KNOWN_STOCKS
        from app.agents import get_pattern_model

        model = get_pattern_model()
        await model.init_db()
        syms = [s.upper() for s in (symbols or list(KNOWN_STOCKS.keys()))]
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        started = time.time()

        samples: list[tuple[list[float], int]] = []
        ok = fail = 0
        for sym in syms:
            try:
                candles, _ = await _fetch_candles(sym, start, end)
            except Exception:
                fail += 1
                continue
            if not candles or len(candles) < 20 + horizon:
                continue
            for i in range(15, len(candles) - horizon, max(1, stride)):
                window = candles[: i + 1]
                fp = build_fingerprint(window)
                if fp is None:
                    continue
                c0 = float(candles[i]["close"])
                cf = float(candles[i + horizon]["close"])
                if c0 <= 0:
                    continue
                ret = (cf - c0) / c0 * 100
                samples.append((fp, 1 if ret >= _LABEL_UP else 0))
            ok += 1

        if not samples:
            _last_train = {"status": "no_data", "symbols": len(syms)}
            return _last_train

        res = await model.learn_batch(samples, epochs=3, note=f"{trigger}:{len(syms)}syms/h{horizon}")
        _last_train = {
            "status": "ok", "trigger": trigger, "symbols_ok": ok, "symbols_failed": fail,
            "samples": len(samples), "batch_accuracy": res.get("batch_accuracy"),
            "recent_accuracy": res.get("recent_accuracy", res.get("ema_accuracy")),
            "n_samples_total": res.get("n_samples"),
            "duration_secs": round(time.time() - started, 1),
            "horizon": horizon, "lookback_days": lookback_days,
        }
        logger.info("pattern training done: %s", _last_train)
        return _last_train
