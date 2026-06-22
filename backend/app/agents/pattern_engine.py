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

# Composite-score grade bands.
# Spread wider than the old 0.36/0.41/0.47 so borderline signals don't oscillate
# between grades on tiny noise.  The dynamic weighting below (GBM 1.5×, memory
# floor-scaled) shifts the effective distribution upward vs. equal weights, so
# the bands are raised accordingly.
_BANDS = {
    "A": float(os.getenv("PATTERN_GRADE_A", "0.52")),   # clear bullish consensus
    "B": float(os.getenv("PATTERN_GRADE_B", "0.44")),   # moderate bullish lean
    "C": float(os.getenv("PATTERN_GRADE_C", "0.37")),   # weak lean — traded only on loose gate
}
_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}

# Memory weight multipliers by similarity floor.
# High-floor retrieval (0.65+) is genuinely similar — trust it strongly.
# Low-floor (0.45) means we relaxed the bar to find cases; down-weight accordingly.
_MEM_FLOOR_WEIGHT = {0.65: 1.8, 0.55: 1.0, 0.45: 0.4}


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
                     with_forecast: bool = False, horizon: int | None = None,
                     exclude_memory_sources: set | None = None) -> dict:
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
        mem_floor: float | None = None
        symbol_local = False
        try:
            mem = await get_memory().query(fp, symbol=symbol, regime=classify_regime(candles),
                                           exclude_sources=exclude_memory_sources)
            buy = (mem.get("per_action") or {}).get("BUY") or {}
            mw = buy.get("win_rate")
            ms = int(buy.get("n", 0))
            mem_floor = mem.get("actual_floor")
            symbol_local = bool(mem.get("symbol_local", False))
        except Exception as exc:
            logger.debug("pattern memory query failed: %s", exc)

        # ── Signal blending with reliability-based weights ─────────────────────
        # Linear model is the base (weight 1.0).
        # GBM is non-linear and typically more accurate — give it 1.5× weight when
        # trained so it has more say than the linear baseline.
        # Memory win-rate weight scales with retrieval quality:
        #   - High-similarity floor (0.65): genuinely similar cases → weight 1.8
        #   - Mid floor (0.55): decent similarity → weight 1.0
        #   - Low floor (0.45): relaxed bar, noisy neighbours → weight 0.4
        # Within each floor tier, further scale by sample mass (8→30+ samples)
        # and give a 20% bonus for symbol-local cases (more directly comparable).
        parts: list[tuple[float, float]] = [(p_up, 1.0)]   # (value, weight)
        gbm_p = None
        try:
            from .registry import get_registry, is_enabled
            reg = await get_registry()
            if is_enabled(reg, "gbm"):
                from app.agents import get_gbm_model
                gm = get_gbm_model()
                await gm.init_db()
                if gm.is_trained:
                    gp = gm.predict_fp(fp)
                    if gp:
                        gbm_p = float(gp["p_up"])
                        parts.append((gbm_p, 1.5))   # GBM outperforms linear model
        except Exception as exc:
            logger.debug("gbm blend skipped: %s", exc)

        if ms >= _MIN_MEM_SAMPLES and mw is not None:
            floor_w = _MEM_FLOOR_WEIGHT.get(mem_floor, 0.4) if mem_floor else 0.4
            sample_scale = min(2.0, ms / 15)        # 0.53 at 8 samples → 2.0 at 30+
            mem_w = floor_w * sample_scale
            if symbol_local:
                mem_w *= 1.2                         # symbol-local cases are more comparable
            parts.append((float(mw), mem_w))

        wsum = sum(w for _, w in parts) or 1.0
        score = sum(v * w for v, w in parts) / wsum
        out = {"grade": _grade(score), "p_up": round(p_up, 3),
               "gbm_p_up": (round(gbm_p, 3) if gbm_p is not None else None),
               "memory_winrate": (round(float(mw), 3) if mw is not None else None),
               "memory_samples": ms, "memory_floor": mem_floor,
               "score": round(score, 3), "ok": True}
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
