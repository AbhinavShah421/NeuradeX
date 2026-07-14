"""Anomaly / Trap Detector — flags abnormal bars the ensemble should not trade.

Sharp news spikes, illiquid gap-ups and bull/bear traps share a fingerprint:
the latest bar is a statistical outlier in return, range and volume versus the
symbol's own recent behaviour. This model fits a scikit-learn IsolationForest on
a rolling window of per-bar features (no training run, no labels — it learns the
symbol's *own* normal each call) and scores the latest bar. When that bar is an
outlier, the ensemble vetoes new directional entries (see EnsembleEngine).

CPU-only and cheap: a tiny forest over ~60 rows takes well under a millisecond.
"""
from __future__ import annotations
import math

import numpy as np

from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_CONTAM = 0.08   # expected outlier fraction
_MIN_ROWS = 40


def _features(candles: list[dict]) -> np.ndarray | None:
    closes = [c.get("close") for c in candles]
    highs = [c.get("high") for c in candles]
    lows = [c.get("low") for c in candles]
    vols = [c.get("volume") or 0 for c in candles]
    rows = []
    for i in range(1, len(closes)):
        if not closes[i] or not closes[i - 1]:
            continue
        ret = math.log(closes[i] / closes[i - 1]) if closes[i] > 0 and closes[i - 1] > 0 else 0.0
        hi, lo = highs[i] or closes[i], lows[i] or closes[i]
        rng = (hi - lo) / closes[i] if closes[i] else 0.0
        body = abs(closes[i] - closes[i - 1]) / closes[i] if closes[i] else 0.0
        rows.append([ret, rng, body, float(vols[i])])
    if len(rows) < _MIN_ROWS:
        return None
    arr = np.array(rows, dtype=float)
    # Log-scale volume so a single huge bar doesn't dominate the split geometry.
    arr[:, 3] = np.log1p(np.clip(arr[:, 3], 0, None))
    return arr


class AnomalyDetectorAgent(BaseAgent):
    name = "anomaly"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        # Live feeds leave the newest (still-forming) candle's volume at 0 until
        # the minute seals — and the just-sealed bar's volume is enriched
        # asynchronously, so at decision time the last 1-2 bars often read 0 even
        # on liquid symbols. A zero in the volume feature made those bars
        # guaranteed outliers (scores 9-13 vs ~0.1) and vetoed ~half of all
        # decisions on 2026-07-13/14. Trim trailing zero-volume bars (bounded, so
        # a genuinely dead tape still gets scored) and rate the last filled bar.
        if any((c.get("volume") or 0) for c in candles[-20:]):
            trimmed = 0
            while (len(candles) >= 2 and trimmed < 5
                   and not (candles[-1].get("volume") or 0)):
                candles = candles[:-1]
                trimmed += 1
        feats = _features(candles)
        if feats is None:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for anomaly detection",
                               indicators={"anomaly": False})
        try:
            from sklearn.ensemble import IsolationForest
            clf = IsolationForest(n_estimators=60, contamination=_CONTAM,
                                  random_state=42, n_jobs=1)
            clf.fit(feats)
            raw = clf.score_samples(feats)        # higher = more normal
            latest = float(raw[-1])
            # Normalize: how far the latest sits below the window's typical score.
            med = float(np.median(raw))
            mad = float(np.median(np.abs(raw - med))) or 1e-6
            dev = (med - latest) / mad            # >0 means more anomalous than typical
            is_outlier = bool(clf.predict(feats[-1:])[0] == -1)
        except Exception as exc:
            logger.debug("anomaly model failed: %s", exc)
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Anomaly model unavailable", indicators={"anomaly": False})

        score = round(max(0.0, dev), 2)
        if is_outlier and dev >= 2.0:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.8,
                               reasoning=f"Abnormal bar (outlier, score {score}) — likely trap; stand aside.",
                               indicators={"anomaly": True, "anomaly_score": score,
                                           "ret": round(feats[-1][0], 4), "range": round(feats[-1][1], 4)})
        return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.4,
                           reasoning=f"Bar within normal bounds (score {score}).",
                           indicators={"anomaly": False, "anomaly_score": score})
