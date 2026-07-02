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
# High-confidence ("sure") predictions: |p-0.5| ≥ this. Accuracy on this subset is
# the honest path toward a high hit-rate — the model abstains on uncertain patterns.
_HC_MARGIN = 0.30      # i.e. p ≥ 0.80 or p ≤ 0.20

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_model_state (
    id            INT PRIMARY KEY DEFAULT 1,
    weights       JSONB,
    bias          DOUBLE PRECISION DEFAULT 0,
    n_samples     BIGINT DEFAULT 0,
    n_correct     BIGINT DEFAULT 0,
    ema_accuracy  DOUBLE PRECISION,
    hc_samples    BIGINT DEFAULT 0,
    hc_correct    BIGINT DEFAULT 0,
    version       INT DEFAULT 1,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
"""
_CURVE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_model_curve (
    id              SERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ DEFAULT NOW(),
    n_samples       BIGINT,
    ema_accuracy    DOUBLE PRECISION,
    batch_accuracy  DOUBLE PRECISION,
    hc_accuracy     DOUBLE PRECISION,
    hc_coverage     DOUBLE PRECISION,
    batch_size      INT,
    note            TEXT
);
"""
_MIGRATE = [
    "ALTER TABLE pattern_model_state ADD COLUMN IF NOT EXISTS hc_samples BIGINT DEFAULT 0",
    "ALTER TABLE pattern_model_state ADD COLUMN IF NOT EXISTS hc_correct BIGINT DEFAULT 0",
    "ALTER TABLE pattern_model_curve ADD COLUMN IF NOT EXISTS hc_accuracy DOUBLE PRECISION",
    "ALTER TABLE pattern_model_curve ADD COLUMN IF NOT EXISTS hc_coverage DOUBLE PRECISION",
]


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
        self.hc_samples = 0          # high-confidence predictions made
        self.hc_correct = 0          # of those, how many were right
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
        for stmt in _MIGRATE:
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception:
                logger.debug("pattern_model schema migration statement skipped (likely already applied): %s", stmt, exc_info=True)
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT weights, bias, n_samples, n_correct, ema_accuracy, hc_samples, hc_correct "
                "FROM pattern_model_state WHERE id=1"
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
                self.hc_samples = int(row[5] or 0)
                self.hc_correct = int(row[6] or 0)
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
                INSERT INTO pattern_model_state (id, weights, bias, n_samples, n_correct, ema_accuracy, hc_samples, hc_correct, updated_at)
                VALUES (1, CAST(:w AS jsonb), :b, :n, :c, :e, :hn, :hc, NOW())
                ON CONFLICT (id) DO UPDATE SET
                  weights=CAST(:w AS jsonb), bias=:b, n_samples=:n, n_correct=:c,
                  ema_accuracy=:e, hc_samples=:hn, hc_correct=:hc, updated_at=NOW()
            """), {"w": json.dumps([round(float(x), 6) for x in self.w]), "b": float(self.b),
                   "n": int(self.n_samples), "c": int(self.n_correct),
                   "e": (float(self.ema_accuracy) if self.ema_accuracy is not None else None),
                   "hn": int(self.hc_samples), "hc": int(self.hc_correct)})
        self._dirty = False
        self._last_save = time.time()

    async def _snapshot_curve(self, batch_acc: float, hc_acc: Optional[float], hc_cov: float,
                              batch_size: int, note: str) -> None:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO pattern_model_curve (n_samples, ema_accuracy, batch_accuracy, hc_accuracy, hc_coverage, batch_size, note)
                VALUES (:n, :e, :ba, :ha, :hcov, :bs, :note)
            """), {"n": int(self.n_samples),
                   "e": (float(self.ema_accuracy) if self.ema_accuracy is not None else None),
                   "ba": round(float(batch_acc), 4),
                   "ha": (round(float(hc_acc), 4) if hc_acc is not None else None),
                   "hcov": round(float(hc_cov), 4), "bs": int(batch_size), "note": note})

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

    def weights_payload(self) -> dict:
        """Export the learned weights so another service (the scanner) can score
        patterns locally without a round-trip per stock."""
        return {"weights": [round(float(x), 6) for x in self.w], "bias": round(float(self.b), 6),
                "dim": FINGERPRINT_DIM, "trained": self.n_samples > 0,
                "hc_margin": _HC_MARGIN, "n_samples": self.n_samples}

    # ── learning ─────────────────────────────────────────────────────────────────
    def _step(self, x: np.ndarray, y: int) -> bool:
        """One SGD step on logloss; returns whether the pre-update call was correct."""
        p = self._p(x)
        correct = (p >= 0.5) == (y == 1)
        if abs(p - 0.5) >= _HC_MARGIN:          # the model was "sure"
            self.hc_samples += 1
            self.hc_correct += 1 if correct else 0
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

        # Pre-training accuracy on this fresh batch ≈ generalisation signal. Also
        # measure it on just the high-confidence subset (the selective hit-rate).
        pre_correct = hc_n = hc_hit = 0
        for x, y in X:
            p = self._p(x)
            ok = (p >= 0.5) == (y == 1)
            pre_correct += 1 if ok else 0
            if abs(p - 0.5) >= _HC_MARGIN:
                hc_n += 1
                hc_hit += 1 if ok else 0
        batch_acc = pre_correct / len(X)
        hc_acc = (hc_hit / hc_n) if hc_n else None
        hc_cov = hc_n / len(X)

        rng = np.random.default_rng()
        for _ in range(max(1, epochs)):
            order = rng.permutation(len(X))
            for i in order:
                x, y = X[i]
                self._step(x, y)
        self._dirty = True
        await self.save(force=True)
        await self._snapshot_curve(batch_acc, hc_acc, hc_cov, len(X), note)
        logger.info("pattern model trained: +%d samples, batch_acc %.1f%%, high-conf %.1f%% (cov %.0f%%), ema %.1f%%",
                    len(X), batch_acc * 100, (hc_acc or 0) * 100, hc_cov * 100, (self.ema_accuracy or 0) * 100)
        return {"status": "ok", "added": len(X), "batch_accuracy": round(batch_acc, 4),
                "high_conf_accuracy": (round(hc_acc, 4) if hc_acc is not None else None),
                "high_conf_coverage": round(hc_cov, 4),
                "ema_accuracy": round(self.ema_accuracy or 0.0, 4), "n_samples": self.n_samples}

    # ── reporting ─────────────────────────────────────────────────────────────────
    async def stats(self) -> dict:
        await self.init_db()
        lifetime = (self.n_correct / self.n_samples) if self.n_samples else None
        hc_acc = (self.hc_correct / self.hc_samples) if self.hc_samples else None
        hc_cov = (self.hc_samples / self.n_samples) if self.n_samples else None
        return {
            "n_samples": self.n_samples,
            "lifetime_accuracy": round(lifetime, 4) if lifetime is not None else None,
            "recent_accuracy": round(self.ema_accuracy, 4) if self.ema_accuracy is not None else None,
            "high_conf_accuracy": round(hc_acc, 4) if hc_acc is not None else None,
            "high_conf_coverage": round(hc_cov, 4) if hc_cov is not None else None,
            "high_conf_margin": _HC_MARGIN,
            "dim": FINGERPRINT_DIM,
            "trained": self.n_samples > 0,
        }

    async def curve(self, limit: int = 200) -> list[dict]:
        await self.init_db()
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT ts, n_samples, ema_accuracy, batch_accuracy, hc_accuracy, hc_coverage, batch_size, note "
                "FROM pattern_model_curve ORDER BY id ASC"
            ))).fetchall()
        pts = [{
            "ts": r[0].isoformat() if r[0] else None,
            "n_samples": int(r[1] or 0),
            "recent_accuracy": round(float(r[2]), 4) if r[2] is not None else None,
            "batch_accuracy": round(float(r[3]), 4) if r[3] is not None else None,
            "hc_accuracy": round(float(r[4]), 4) if r[4] is not None else None,
            "hc_coverage": round(float(r[5]), 4) if r[5] is not None else None,
            "batch_size": int(r[6] or 0), "note": r[7],
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


_UNIVERSE_OFFSET_KEY = "ai_engine:pattern_train_offset"
_GBM_OFFSET_KEY = "ai_engine:gbm_train_offset"


async def _full_universe() -> list[str]:
    """The full NSE universe the scanner discovered (~2100 symbols), so pattern
    training isn't limited to the curated list. Falls back to KNOWN_STOCKS."""
    try:
        from app.utils.redis_client import cache_get
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        ist = _dt.now(_tz(_td(hours=5, minutes=30))).strftime("%Y-%m-%d")
        raw = await cache_get(f"ai_engine:scan_universe:{ist}")
        if raw:
            uni = json.loads(raw)
            syms = list(uni.keys()) if isinstance(uni, dict) else list(uni)
            if syms:
                return [s.upper() for s in syms]
    except Exception as exc:
        logger.debug("full universe load failed: %s", exc)
    from app.api.agent import KNOWN_STOCKS
    return [s.upper() for s in KNOWN_STOCKS.keys()]


async def train_pattern_model(symbols: list[str] | None = None, lookback_days: int = 365,
                              horizon: int = 3, stride: int = 1, max_symbols: int = 400,
                              trigger: str = "manual") -> dict:
    """Backtest-as-pattern-trainer: walk historical candles, and for each window
    build the pattern fingerprint (no lookahead) and label it by the realised
    forward return `horizon` bars later. ONLY the pattern and its outcome are
    used — no indicators/news/RL — then feed the pattern model.

    Trains a rotating `max_symbols`-sized slice of the FULL NSE universe, advancing
    a persisted offset each run, so over successive runs it covers every stock and
    the learned sample count keeps growing."""
    global _last_train
    if _train_lock.locked():
        return {"status": "already_running"}

    async with _train_lock:
        from app.api.backtest import _fetch_candles
        from app.agents import get_pattern_model
        from app.utils.redis_client import cache_get, cache_set

        model = get_pattern_model()
        await model.init_db()

        if symbols:
            syms = [s.upper() for s in symbols]
            covered_note = f"{len(syms)} given"
        else:
            universe = await _full_universe()
            total = len(universe)
            try:
                offset = int(await cache_get(_UNIVERSE_OFFSET_KEY) or 0) % max(1, total)
            except Exception:
                logger.debug("Failed to read pattern-model universe offset from cache; starting at 0", exc_info=True)
                offset = 0
            if max_symbols and max_symbols < total:
                syms = [universe[(offset + i) % total] for i in range(max_symbols)]
                new_off = (offset + max_symbols) % total
            else:
                syms = universe
                new_off = 0
            try:
                await cache_set(_UNIVERSE_OFFSET_KEY, str(new_off), expire=86400 * 30)
            except Exception:
                logger.debug("Failed to persist pattern-model universe offset to cache", exc_info=True)
            covered_note = f"{len(syms)}/{total} (offset {offset}→{new_off})"

        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        started = time.time()

        samples: list[tuple[list[float], int]] = []
        ok = fail = 0
        for sym in syms:
            try:
                candles, _ = await _fetch_candles(sym, start, end)
            except Exception:
                logger.debug("candle fetch failed for %s during pattern-model training", sym, exc_info=True)
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

        res = await model.learn_batch(samples, epochs=3, note=f"{trigger}:{covered_note}/h{horizon}")
        _last_train = {
            "status": "ok", "trigger": trigger, "symbols_ok": ok, "symbols_failed": fail,
            "universe_slice": covered_note,
            "samples": len(samples), "batch_accuracy": res.get("batch_accuracy"),
            "high_conf_accuracy": res.get("high_conf_accuracy"),
            "high_conf_coverage": res.get("high_conf_coverage"),
            "ema_accuracy": res.get("ema_accuracy"),
            "n_samples_total": res.get("n_samples"),
            "duration_secs": round(time.time() - started, 1),
            "horizon": horizon, "lookback_days": lookback_days,
        }
        logger.info("pattern training done: %s", _last_train)
        return _last_train


async def train_gbm_model(symbols: list[str] | None = None, lookback_days: int = 365,
                          horizon: int = 3, stride: int = 2, max_symbols: int = 250,
                          trigger: str = "manual") -> dict:
    """Train the Gradient-Boosted P(up) model on the SAME (fingerprint → realised
    forward-return) samples the pattern model learns from, so it's a like-for-like
    non-linear upgrade. Rotates through the universe like train_pattern_model."""
    if _train_lock.locked():
        return {"status": "already_running"}
    async with _train_lock:
        from app.api.backtest import _fetch_candles
        from app.agents import get_gbm_model
        from app.utils.redis_client import cache_get, cache_set

        gbm = get_gbm_model()
        await gbm.init_db()

        if symbols:
            syms = [s.upper() for s in symbols]
            covered = f"{len(syms)} given"
        else:
            universe = await _full_universe()
            total = len(universe)
            try:
                offset = int(await cache_get(_GBM_OFFSET_KEY) or 0) % max(1, total)
            except Exception:
                logger.debug("Failed to read GBM universe offset from cache; starting at 0", exc_info=True)
                offset = 0
            if max_symbols and max_symbols < total:
                syms = [universe[(offset + i) % total] for i in range(max_symbols)]
                new_off = (offset + max_symbols) % total
            else:
                syms, new_off = universe, 0
            try:
                await cache_set(_GBM_OFFSET_KEY, str(new_off), expire=86400 * 30)
            except Exception:
                logger.debug("Failed to persist GBM universe offset to cache", exc_info=True)
            covered = f"{len(syms)}/{total} (offset {offset}→{new_off})"

        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        started = time.time()
        samples: list[tuple[list[float], int]] = []
        ok = fail = 0
        for sym in syms:
            try:
                candles, _ = await _fetch_candles(sym, start, end)
            except Exception:
                logger.debug("candle fetch failed for %s during GBM training", sym, exc_info=True)
                fail += 1
                continue
            if not candles or len(candles) < 20 + horizon:
                continue
            for i in range(15, len(candles) - horizon, max(1, stride)):
                fp = build_fingerprint(candles[: i + 1])
                if fp is None:
                    continue
                c0 = float(candles[i]["close"]); cf = float(candles[i + horizon]["close"])
                if c0 <= 0:
                    continue
                ret = (cf - c0) / c0 * 100
                samples.append((fp, 1 if ret >= _LABEL_UP else 0))
            ok += 1

        if len(samples) < 200:
            return {"status": "no_data", "samples": len(samples), "symbols": len(syms)}
        res = await gbm.fit(samples, note=f"{trigger}:{covered}/h{horizon}")
        res.update({"symbols_ok": ok, "symbols_failed": fail, "universe_slice": covered,
                    "duration_secs": round(time.time() - started, 1)})
        logger.info("gbm training done: %s", res)
        return res


def _seconds_until_hour_ist(hour: int) -> float:
    from datetime import timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    target = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def gbm_autotrain_loop() -> None:
    """Background task: retrain the GBM once daily, advancing the universe offset so
    it covers the whole market over successive nights and keeps strengthening."""
    from app.config import settings
    if not getattr(settings, "GBM_AUTOTRAIN_ENABLED", True):
        logger.info("GBM auto-retrain disabled via config")
        return
    while True:
        try:
            wait = _seconds_until_hour_ist(getattr(settings, "GBM_AUTOTRAIN_HOUR_IST", 3))
            logger.info("Next GBM auto-retrain in %.0f min", wait / 60)
            await asyncio.sleep(wait)
            await train_gbm_model(
                max_symbols=getattr(settings, "GBM_AUTOTRAIN_MAX_SYMBOLS", 250),
                trigger="scheduled")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Scheduled GBM retrain error: %s", exc)
            await asyncio.sleep(3600)  # back off an hour on failure
