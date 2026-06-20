"""GBMAgent — the ensemble voice for the Gradient-Boosted P(up) model.

Wraps the learned non-linear classifier (gbm_model.py) as a BaseAgent so it votes
alongside the other models. It abstains (HOLD) until the model has been trained
and when its probability is near 0.5 (no edge)."""
from __future__ import annotations

from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)


class GBMAgent(BaseAgent):
    name = "gbm"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        from .gbm_model import get_gbm_model
        model = get_gbm_model()
        try:
            await model.init_db()
        except Exception as exc:
            logger.debug("gbm init failed: %s", exc)
        if not model.is_trained:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="GBM model not trained yet — train it from the AI Models panel.",
                               indicators={"trained": False})
        pred = model.predict_candles(candles)
        if not pred:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Not enough candles for a pattern fingerprint",
                               indicators={"trained": True})
        p = pred["p_up"]
        ind = {"trained": True, "p_up": p}
        if p >= 0.58:
            action, conf = "BUY", min(0.95, 0.5 + (p - 0.5) * 1.6)
            reason = f"GBM P(up) {p:.0%} — learned non-linear pattern favours upside."
        elif p <= 0.42:
            action, conf = "SELL", min(0.95, 0.5 + (0.5 - p) * 1.6)
            reason = f"GBM P(up) {p:.0%} — learned pattern favours downside."
        else:
            action, conf = "HOLD", 0.45
            reason = f"GBM P(up) {p:.0%} — no clear edge."
        return AgentSignal(agent_name=self.name, action=action, confidence=round(conf, 3),
                           reasoning=reason, indicators=ind)
