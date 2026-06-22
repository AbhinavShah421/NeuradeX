"""Ensemble Decision Engine — runs all agents in parallel, weighted voting."""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from datetime import datetime
from .base import AgentSignal, BaseAgent, EnsembleDecision
from .registry import get_registry, is_enabled, weight_override
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# ── Learned-weight cache ──────────────────────────────────────────────────────
# LearningSystem syncs per-agent weights to Redis after every closed trade.
# The ensemble reads them here and uses them as the live decision weights,
# so the system genuinely gets better with each trade outcome.
_LEARNED_WEIGHTS_KEY = "ai_engine:agent_weights"
_lw_cache: dict = {"data": {}, "ts": 0.0}
_LW_TTL = 30.0   # seconds between Redis refreshes


async def _get_learned_weights() -> dict[str, float]:
    now = time.time()
    if _lw_cache["data"] and (now - _lw_cache["ts"]) < _LW_TTL:
        return _lw_cache["data"]
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_LEARNED_WEIGHTS_KEY)
        if raw:
            _lw_cache.update({"data": json.loads(raw), "ts": now})
            return _lw_cache["data"]
    except Exception:
        pass
    return {}

DEFAULT_WEIGHTS: dict[str, float] = {
    "technical":  1.0,
    "pattern":    1.0,
    "momentum":   1.0,
    "volatility": 1.0,
    "sentiment":  1.0,
    "rl":         0.8,  # lower until RL proves itself
    "memory":     1.3,  # historical precedent carries extra weight in the vote
}

# Agents that require real-time data with no historical equivalent.
# Sentiment IS included in replay/backtest — it reads a date-keyed Redis key
# (ai_engine:sentiment:{sym}:{date}) pre-fetched by the autopilot's historical
# sentiment ranking, and falls back to a background Google News dated-query fetch
# on the first candle so the next call has a real signal.
_REPLAY_SKIP_AGENTS: frozenset[str] = frozenset()

# In replay/backtest, emphasise price-pattern agents whose signals are derived
# purely from OHLCV data; keep sentiment at its normal weight since it now uses
# date-specific news, not today's feed.
_REPLAY_WEIGHT_BOOST: dict[str, float] = {
    "pattern":   1.5,
    "technical": 1.2,
    "momentum":  1.1,
    "gbm":       1.2,
}

# Evidence gate: a non-HOLD decision is only allowed to fire if the Pattern
# Memory bank has enough similar past cases AND they won often enough.
_MEM_MIN_SAMPLES    = 8      # need at least this many per-action cases to gate
_MEM_GATE_WINRATE   = 0.50   # below this, veto the trade → HOLD (abstain)
_MEM_STRONG_WINRATE = 0.65   # above this, actively boost confidence


class EnsembleEngine:
    def __init__(self, agents: list[BaseAgent]) -> None:
        self.agents   = agents
        self._weights = dict(DEFAULT_WEIGHTS)

    def update_weights(self, weights: dict[str, float]) -> None:
        self._weights.update(weights)

    @staticmethod
    def _apply_regime(signals: list[AgentSignal]) -> str:
        """If the regime model is present, scale momentum vs mean-reversion vote
        weights by the detected regime. Returns the regime label (or 'unknown')."""
        rs = next((s for s in signals if s.agent_name == "regime"), None)
        if rs is None:
            return "unknown"
        regime = (rs.indicators or {}).get("regime", "unknown")
        mult = {
            "trend":    {"momentum": 1.35, "meanrev": 0.5},
            "chop":     {"momentum": 0.6,  "meanrev": 1.4},
            "range":    {"momentum": 0.6,  "meanrev": 1.4},
            "high_vol": {"momentum": 0.7,  "meanrev": 0.7, "technical": 0.8},
        }.get(regime, {})
        if mult:
            for s in signals:
                if s.agent_name in mult:
                    s.weight *= mult[s.agent_name]
        return regime

    async def decide(
        self,
        symbol:  str,
        candles: list[dict],
        context: dict,
    ) -> EnsembleDecision:
        """Run all agents in parallel, combine with weighted voting. Each model is
        independently enable/weight-controlled via the model registry."""

        reg = await get_registry()
        learned = await _get_learned_weights()   # weights updated after every trade
        mode = context.get("mode", "paper")
        active = [a for a in self.agents if is_enabled(reg, a.name)]
        if mode in ("replay", "backtest"):
            active = [a for a in active if a.name not in _REPLAY_SKIP_AGENTS]

        raw = await asyncio.gather(
            *[a.analyze(symbol, candles, context) for a in active],
            return_exceptions=True,
        )

        signals: list[AgentSignal] = []
        for result, agent in zip(raw, active):
            if isinstance(result, Exception):
                logger.warning("Agent %s error: %s", agent.name, result)
                signals.append(AgentSignal(
                    agent_name=agent.name, action="HOLD",
                    confidence=0.30, reasoning=f"Error: {result}",
                ))
            else:
                # Weight priority: manual registry override > learned from outcomes > hardcoded default.
                # This means every closed trade nudges the weights, and the next decision
                # immediately reflects that learning — without any restart.
                ov = weight_override(reg, result.agent_name)
                if ov is not None:
                    result.weight = ov
                else:
                    result.weight = learned.get(result.agent_name) or self._weights.get(result.agent_name, 1.0)
                signals.append(result)

        # ── Replay mode: boost price-pattern agents ─────────────────────────────
        # In historical replay, only OHLCV-derived agents have valid signals.
        # Up-weight the ones that were kept so they dominate the vote.
        if mode in ("replay", "backtest"):
            for s in signals:
                boost = _REPLAY_WEIGHT_BOOST.get(s.agent_name)
                if boost:
                    s.weight = (s.weight or 1.0) * boost

        # ── Regime-aware reweighting ────────────────────────────────────────────
        # The Market-Regime model tilts the vote: trust momentum in trends and
        # mean-reversion in chop; damp directional voices in high-vol regimes.
        regime = self._apply_regime(signals)

        # Weighted vote
        vote: dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        for s in signals:
            vote[s.action] += s.confidence * s.weight

        total    = sum(vote.values()) or 1.0
        vote_pct = {k: v / total for k, v in vote.items()}
        action   = max(vote, key=lambda k: vote[k])
        confidence = 0.30 + vote_pct[action] * 0.65

        # Agreement score
        agreers   = sum(1 for s in signals if s.action == action)
        agreement = agreers / len(signals) if signals else 0.0

        # Risk score from volatility agent
        risk_score = 0.50
        for s in signals:
            if s.agent_name == "volatility":
                risk_score = float(s.indicators.get("risk_score", 0.50))
                break

        # ── Pattern-memory evidence gate ────────────────────────────────────────
        # This is the lever that pushes win-rate up: only act when similar past
        # situations actually paid off; otherwise abstain (HOLD). When evidence is
        # strong, boost confidence; when memory is empty (cold start), stay neutral.
        mem = next((s for s in signals if s.agent_name == "memory"), None)
        memory_note = ""
        if mem is not None:
            mi = mem.indicators or {}
            if action in ("BUY", "SELL"):
                # Gate fires only when we have at least _MEM_MIN_SAMPLES cases of
                # this SPECIFIC action — not just total similarity matches. This
                # prevents the bank from vetoing BUY when it only has 2 BUY precedents
                # and 25 SELL/HOLD ones. Cold-start (< _MEM_MIN_SAMPLES action cases)
                # lets the other agents decide unimpeded.
                n_action = int(mi.get(f"n_{action}", 0))
                intended_action = action
                if n_action >= _MEM_MIN_SAMPLES:
                    wr = mi.get(f"wr_{action}")
                    if wr is None:
                        action = "HOLD"
                        confidence = 0.55
                        memory_note = f"memory veto: no similar {intended_action} precedent"
                    elif wr < _MEM_GATE_WINRATE:
                        action = "HOLD"
                        confidence = 0.55
                        memory_note = f"memory veto: similar {intended_action} setups won only {wr:.0%}"
                    else:
                        # Scale confidence by how well this action did historically
                        boost = 0.85 + 0.45 * max(0.0, wr - 0.5)
                        confidence = min(0.95, confidence * boost)
                        if wr >= _MEM_STRONG_WINRATE:
                            memory_note = f"memory confirms: {wr:.0%} {intended_action} win-rate ({n_action} cases)"
                        else:
                            memory_note = f"memory ok: {wr:.0%} {intended_action} win-rate ({n_action} cases)"
                else:
                    samples = int(mi.get("sample_count", 0))
                    if samples > 0:
                        memory_note = f"memory cold-start: only {n_action} {action} cases — gate inactive"

        # ── Anomaly / trap veto ─────────────────────────────────────────────────
        # The anomaly model flags abnormal bars (news spikes, illiquid traps). On a
        # flagged bar we refuse to open/flip a directional position.
        anom = next((s for s in signals if s.agent_name == "anomaly"), None)
        anomaly_note = ""
        if anom is not None and (anom.indicators or {}).get("anomaly") and action in ("BUY", "SELL"):
            score = float((anom.indicators or {}).get("anomaly_score", 0.0))
            action = "HOLD"
            confidence = 0.58
            anomaly_note = f"anomaly veto: abnormal price/volume (score {score:.2f})"

        # Override to HOLD in extreme volatility (risk beats everything)
        if risk_score > 0.80 and action != "HOLD":
            action     = "HOLD"
            confidence = 0.60
            reasoning  = f"High volatility risk ({risk_score:.2f}) → forced HOLD"
        else:
            top = sorted(signals, key=lambda s: s.confidence * s.weight, reverse=True)[:2]
            reasoning = " | ".join(
                f"{s.agent_name}: {s.action} {s.confidence:.0%}" for s in top
            )
            if memory_note:
                reasoning = f"{reasoning}  ·  {memory_note}"
            if anomaly_note:
                reasoning = f"{reasoning}  ·  {anomaly_note}"
            if regime and regime != "unknown":
                reasoning = f"{reasoning}  ·  regime: {regime}"

        confidence = round(confidence, 3)
        pred_id = str(uuid.uuid4())

        logger.info("Ensemble decision",
                    extra={"log_type": "ai_engine", "event": "ensemble_decision",
                           "symbol": symbol, "action": action,
                           "confidence": round(confidence, 3),
                           "agreement": round(agreement, 3),
                           "risk_score": round(risk_score, 3),
                           "prediction_id": pred_id,
                           "vote": {k: round(v, 3) for k, v in vote_pct.items()}})

        return EnsembleDecision(
            action          = action,
            confidence      = round(confidence, 3),
            agent_agreement = round(agreement, 3),
            risk_score      = round(risk_score, 3),
            agents          = signals,
            reasoning       = reasoning,
            prediction_id   = pred_id,
            timestamp       = datetime.now(),
        )
