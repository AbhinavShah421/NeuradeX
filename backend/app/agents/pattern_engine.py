"""Unified Pattern AI engine.

One place that turns a candle window into a graded pattern signal, by combining:
  • the Pattern Recognition Model's P(up)  (learned, generalising)
  • the Pattern Memory bank's historical win-rate for similar setups (case-based)

into a single A/B/C/D **pattern grade**. Used to rank patterns and gate trades —
backtesting can be told to enter only A-grade patterns, and the same engine grades
patterns for paper / live / autopilot, so every trading mode shares one brain.
"""
from __future__ import annotations
import os

from app.utils.elk_logger import get_logger
from .fingerprint import build_fingerprint, classify_regime

logger = get_logger(__name__)

_MIN_MEM_SAMPLES = int(os.getenv("PATTERN_MIN_MEM_SAMPLES", "8"))
# Composite-score grade bands (score = blend of model P(up) + memory win-rate).
# Calibrated to the observed score distribution over real buy-trigger windows
# (median ~0.36, p70 ~0.41, p90 ~0.47): A ≈ top decile of setups, B ≈ top 30%,
# C ≈ top half. Env-overridable so the cutoffs can be tuned without a code change.
_BANDS = {
    "A": float(os.getenv("PATTERN_GRADE_A", "0.47")),
    "B": float(os.getenv("PATTERN_GRADE_B", "0.41")),
    "C": float(os.getenv("PATTERN_GRADE_C", "0.36")),
}
_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def _grade(score: float) -> str:
    if score >= _BANDS["A"]:
        return "A"
    if score >= _BANDS["B"]:
        return "B"
    if score >= _BANDS["C"]:
        return "C"
    return "D"


def grade_rank(g: str) -> int:
    return _GRADE_RANK.get(g, 3)


class PatternEngine:
    async def signal(self, candles: list[dict], symbol: str | None = None,
                     with_forecast: bool = False, horizon: int | None = None) -> dict:
        """Graded pattern signal for the latest situation in `candles`.

        with_forecast=True also attaches a Monte-Carlo path forecast (projected
        path / target / stop / uncertainty). It is cheap but NOT free, so callers
        on the full-universe scan leave it off; low-volume decision paths
        (committed / delivery / a session's single symbol) turn it on.
        """
        fp = build_fingerprint(candles)
        if not fp:
            out = {"grade": "D", "p_up": None, "memory_winrate": None,
                   "memory_samples": 0, "score": None, "ok": False}
            if with_forecast:
                out["forecast"] = {"ok": False}
            return out
        from app.agents import get_pattern_model, get_memory
        model = get_pattern_model()
        await model.init_db()
        pred = model.predict_fp(fp)
        p_up = float(pred["p_up"]) if pred else 0.5

        mw = None
        ms = 0
        try:
            mem = await get_memory().query(fp, symbol=symbol, regime=classify_regime(candles))
            buy = (mem.get("per_action") or {}).get("BUY") or {}
            mw = buy.get("win_rate")
            ms = int(buy.get("n", 0))
        except Exception as exc:
            logger.debug("pattern memory query failed: %s", exc)

        # Blend model + memory when memory has enough evidence; else model only.
        if ms >= _MIN_MEM_SAMPLES and mw is not None:
            score = 0.5 * p_up + 0.5 * float(mw)
        else:
            score = p_up
        out = {"grade": _grade(score), "p_up": round(p_up, 3),
               "memory_winrate": (round(float(mw), 3) if mw is not None else None),
               "memory_samples": ms, "score": round(score, 3), "ok": True}
        if with_forecast:
            try:
                from app.agents import get_path_forecaster
                out["forecast"] = get_path_forecaster().forecast(candles, horizon=horizon)
            except Exception as exc:
                logger.debug("forecast failed: %s", exc)
                out["forecast"] = {"ok": False}
        return out


_engine: PatternEngine | None = None


def get_pattern_engine() -> PatternEngine:
    global _engine
    if _engine is None:
        _engine = PatternEngine()
    return _engine
