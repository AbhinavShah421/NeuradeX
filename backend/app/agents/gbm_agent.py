"""GBMAgent — the ensemble voice for the Gradient-Boosted P(up) model.

Wraps the learned non-linear classifier (gbm_model.py) as a BaseAgent so it votes
alongside the other models. It abstains (HOLD) until the model has been trained
and when its probability is near 0.5 (no edge).

Timeframe-aware since 2026-07-07: the daily model answers daily questions, the
intraday model (slot 2) answers 1-min session questions. Asking the daily model
about 1-min fingerprints made it vote SELL on 97% of live bars at coin-flip
accuracy — every intraday fingerprint looks dead-flat at daily scale, and flat
patterns skew down in the daily training set."""
from __future__ import annotations

from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


def _bar_minutes(candles: list[dict]) -> float | None:
    """Infer bar interval from the last two 'HH:MM'[':SS'] time strings.
    Returns None when times are absent/unparseable (treated as daily)."""
    if len(candles) < 2:
        return None
    try:
        def _m(t: str) -> float:
            parts = str(t).split(":")
            if not 2 <= len(parts) <= 3:
                raise ValueError(t)
            return int(parts[0]) * 60 + int(parts[1]) + (int(parts[2]) / 60 if len(parts) == 3 else 0)
        deltas = []
        for a, b in zip(candles[-4:-1], candles[-3:]):
            d = _m(b.get("time")) - _m(a.get("time"))
            if d > 0:
                deltas.append(d)
        return min(deltas) if deltas else None
    except (ValueError, TypeError, AttributeError):
        return None


class GBMAgent(BaseAgent):
    name = "gbm"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        from .gbm_model import get_gbm_model, get_gbm_intraday_model

        bar_min = _bar_minutes(candles)
        intraday = bar_min is not None and bar_min <= 5
        model = get_gbm_intraday_model() if intraday else get_gbm_model()
        try:
            await model.init_db()
        except Exception as exc:
            logger.debug("gbm init failed: %s", exc)
        if not model.is_trained:
            # Honest abstention beats a confidently wrong cross-timeframe vote.
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning=(f"No {model.timeframe} GBM model trained yet — abstaining "
                                          "(train from the AI Models panel)."),
                               indicators={"trained": False, "timeframe": model.timeframe})
        pred = model.predict_candles(candles)
        if not pred:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Not enough candles for a pattern fingerprint",
                               indicators={"trained": True, "timeframe": model.timeframe})
        p = pred["p_up"]
        # Neutral point = the model's own training base rate, not 0.5. A model
        # trained on a red-leaning window has pos_rate ~0.39; p=0.41 is then a
        # MILD POSITIVE lift, and reading it against fixed 0.58/0.42 bands
        # manufactured SELLs out of neutral bars. p_adj re-centres on 0.5 so
        # the historical bands keep their meaning regardless of training mix.
        base = float(model.meta.get("pos_rate") or 0.5)
        p_adj = min(1.0, max(0.0, 0.5 + (p - base)))
        ind = {"trained": True, "p_up": p, "p_adj": round(p_adj, 4),
               "base_rate": base, "timeframe": model.timeframe}
        if p_adj >= 0.58:
            action, conf = "BUY", min(0.95, 0.5 + (p_adj - 0.5) * 1.6)
            reason = (f"GBM[{model.timeframe}] P(up) {p:.0%} vs base {base:.0%} "
                      f"— learned non-linear pattern favours upside.")
        elif p_adj <= 0.42:
            action, conf = "SELL", min(0.95, 0.5 + (0.5 - p_adj) * 1.6)
            reason = (f"GBM[{model.timeframe}] P(up) {p:.0%} vs base {base:.0%} "
                      f"— learned pattern favours downside.")
        else:
            action, conf = "HOLD", 0.45
            reason = f"GBM[{model.timeframe}] P(up) {p:.0%} vs base {base:.0%} — no clear edge."
        return AgentSignal(agent_name=self.name, action=action, confidence=round(conf, 3),
                           reasoning=reason, indicators=ind)
