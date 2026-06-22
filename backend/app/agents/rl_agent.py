"""Reinforcement Learning Agent — Q-learning, self-improving from trade outcomes.

State space (108 states):
  RSI bucket    ×3  (oversold / neutral / overbought)
  MACD sign     ×2  (negative / positive)
  VWAP pos      ×2  (below / above)
  Momentum      ×3  (down / flat / up)
  Vol regime    ×3  (low / medium / high)

Action space: BUY=0, SELL=1, HOLD=2
"""
from __future__ import annotations
import json
import random
import math
from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_Q_KEY      = "ai_engine:rl_qtable"
ACTIONS     = ["BUY", "SELL", "HOLD"]
N_STATES    = 3 * 2 * 2 * 3 * 3  # 108
N_ACTIONS   = 3
LR          = 0.10   # learning rate
GAMMA       = 0.90   # discount factor
EPSILON     = 0.05   # exploration rate


class RLAgent(BaseAgent):
    name = "rl"

    def __init__(self) -> None:
        self._q: list[list[float]] | None = None

    # ── Q-table persistence ───────────────────────────────────────────────────

    async def _load(self) -> list[list[float]]:
        if self._q is not None:
            return self._q
        try:
            from app.utils.redis_client import cache_get
            raw = await cache_get(_Q_KEY)
            if raw:
                self._q = json.loads(raw)
                return self._q
        except Exception:
            pass
        # Initialise: slight prior for HOLD (index 2)
        self._q = [[0.0, 0.0, 0.05] for _ in range(N_STATES)]
        return self._q

    async def _save(self) -> None:
        if self._q is None:
            return
        try:
            from app.utils.redis_client import cache_set
            await cache_set(_Q_KEY, json.dumps(self._q), expire=86400 * 30)
        except Exception as exc:
            logger.debug("RL Q-save skipped: %s", exc)

    # ── State extraction ──────────────────────────────────────────────────────

    def extract_state(self, candles: list[dict]) -> int:
        closes = [c["close"] for c in candles]

        # RSI bucket
        rsi   = self._quick_rsi(closes)
        rsi_b = 0 if rsi < 35 else (2 if rsi > 65 else 1)

        # MACD sign
        macd_s = 1
        if len(closes) >= 26:
            k12  = 2 / 13; k26 = 2 / 27
            e12  = closes[0]; e26 = closes[0]
            for c in closes[1:]:
                e12 = c * k12 + e12 * (1 - k12)
                e26 = c * k26 + e26 * (1 - k26)
            macd_s = 1 if e12 >= e26 else 0

        # VWAP position — volume-weighted to match TechnicalAgent's VWAP
        tv = sum((c["high"] + c["low"] + c["close"]) / 3 * c.get("volume", 1) for c in candles)
        v  = sum(c.get("volume", 1) for c in candles)
        vwap = tv / v if v > 0 else sum(c["close"] for c in candles) / len(candles)
        vwap_p = 1 if closes[-1] >= vwap else 0

        # Momentum (5-bar ROC)
        mom_s = 1
        if len(closes) >= 5:
            roc = (closes[-1] - closes[-5]) / closes[-5] * 100
            mom_s = 0 if roc < -1.0 else (2 if roc > 1.0 else 1)

        # Volatility regime
        vol_r = 1
        if len(candles) >= 10:
            atr_approx = sum(c["high"] - c["low"] for c in candles[-10:]) / 10
            atr_pct    = atr_approx / closes[-1] * 100
            vol_r      = 0 if atr_pct < 0.8 else (2 if atr_pct > 2.0 else 1)

        idx = (rsi_b * 2 * 2 * 3 * 3 +
               macd_s   * 2 * 3 * 3 +
               vwap_p       * 3 * 3 +
               mom_s            * 3 +
               vol_r)
        return min(max(idx, 0), N_STATES - 1)

    # ── Main analysis ─────────────────────────────────────────────────────────

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        if len(candles) < 26:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.35,
                               reasoning="RL warming up (need ≥26 candles)")

        q     = await self._load()
        state = self.extract_state(candles)
        qv    = q[state]

        # Only explore (random action) during historical replay training runs.
        # In paper/live modes the RL agent always exploits its learned Q-table —
        # random trades in a real account are noise, not learning.
        eff_epsilon = EPSILON if context.get("mode") == "replay" else 0.0
        if random.random() < eff_epsilon:
            ai = random.randint(0, N_ACTIONS - 1)
            src = "explore"
        else:
            ai  = qv.index(max(qv))
            src = "exploit"

        action = ACTIONS[ai]

        # Softmax confidence
        max_q = max(qv)
        exps  = [math.exp(v - max_q) for v in qv]
        total = sum(exps)
        probs = [e / total for e in exps]
        conf  = max(0.35, min(0.90, 0.35 + probs[ai] * 0.55))

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(conf, 3),
            reasoning=f"Q-learning ({src}): state={state}, Q[{action}]={qv[ai]:.3f}",
            indicators={
                "state":  state,
                "q_buy":  round(qv[0], 3),
                "q_sell": round(qv[1], 3),
                "q_hold": round(qv[2], 3),
                "source": src,
            },
        )

    # ── Learning update ───────────────────────────────────────────────────────

    async def update(self, state: int, action_idx: int, reward: float, next_state: int) -> None:
        q       = await self._load()
        old_q   = q[state][action_idx]
        max_nq  = max(q[next_state])
        new_q   = old_q + LR * (reward + GAMMA * max_nq - old_q)
        q[state][action_idx] = new_q
        self._q = q
        await self._save()
        logger.info("RL update",
                    extra={"log_type": "ai_engine", "event": "rl_update",
                           "state": state, "action": ACTIONS[action_idx],
                           "old_q": round(old_q, 4), "new_q": round(new_q, 4),
                           "reward": round(reward, 4)})

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _quick_rsi(closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(max(1, len(closes) - period - 1), len(closes))]
        gains  = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        ag = sum(gains[:period]) / period if gains else 0.0
        al = sum(losses[:period]) / period if losses else 0.0
        return 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
