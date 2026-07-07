"""Mean-Reversion Agent — adaptive thresholds via Ornstein-Uhlenbeck half-life.

v2 changes from the hardcoded version:
  • OU half-life: OLS regression ΔP ~ P_{t-1} estimates how fast THIS symbol
    reverts to its mean, in bars. κ = -slope;  HL = ln(2) / κ.
  • Thresholds adapt per half-life:
      HL ≤ 8  bars  → z_entry 0.7,  z_strong 1.2   (fast reverter, enter early)
      HL 9-15 bars  → z_entry 1.0,  z_strong 1.6
      HL 16-25 bars → z_entry 1.2,  z_strong 2.0   (old hardcoded default)
      HL 26-40 bars → z_entry 1.6,  z_strong 2.5
      HL > 40 bars  → z_entry 2.0,  z_strong 3.0   (near-trending — very cautious)
      HL > 60 bars  → abstain entirely (stock is trending, not reverting)
  • RSI thresholds adapt symmetrically — fast reverters don't need RSI extremes.
  • Velocity damping: if price is still accelerating away from the mean when
    z looks attractive, halve the score — avoids falling-knife entries.
  • Half-life exposed in indicators so the UI can show the estimated reversion speed.
"""
from __future__ import annotations
import math

from .base import AgentSignal, BaseAgent


# ── Ornstein-Uhlenbeck half-life ──────────────────────────────────────────────

def _ou_half_life(closes: list[float]) -> float:
    """Estimate mean-reversion half-life (in bars) via OLS.

    Regresses ΔP_t on P_{t-1}:  ΔP = a + b·P_{t-1}
    κ = -b  (mean-reversion speed).
    HL = ln(2) / κ

    Returns 200 (effectively ∞) if the series is trending (b ≥ 0).
    Clamped to [2, 200] to keep thresholds sensible.
    """
    n = len(closes)
    if n < 20:
        return 20.0
    prices  = closes[-min(n, 60):]   # use last 60 bars max for recency
    T       = len(prices)
    delta_p = [prices[i] - prices[i - 1] for i in range(1, T)]
    lagged  = prices[:-1]

    m_l  = sum(lagged)  / (T - 1)
    m_d  = sum(delta_p) / (T - 1)
    cov  = sum((lagged[i] - m_l) * (delta_p[i] - m_d) for i in range(T - 1)) / (T - 1)
    var  = sum((lagged[i] - m_l) ** 2 for i in range(T - 1)) / (T - 1)

    if var < 1e-12:
        return 20.0
    b = cov / var     # OLS slope;  κ = -b  should be > 0 for mean-reverting

    if b >= 0.0:
        return 200.0  # trending series — mean reversion won't work here

    kappa     = -b
    half_life = math.log(2) / kappa
    return max(2.0, min(200.0, half_life))


def _z_thresholds(hl: float) -> tuple[float, float]:
    """(z_mild, z_strong) based on half-life in bars."""
    if   hl <=  8: return 0.7, 1.2
    elif hl <= 15: return 1.0, 1.6
    elif hl <= 25: return 1.2, 2.0
    elif hl <= 40: return 1.6, 2.5
    else:          return 2.0, 3.0


def _rsi_thresholds(hl: float) -> tuple[float, float]:
    """(rsi_strong_oversold, rsi_mild_oversold) for BUY side.
    SELL side mirrors: 100 - value.
    Fast reverters don't need extreme RSI to trigger; slow reverters do.
    """
    if   hl <= 15: return 32, 40
    elif hl <= 30: return 27, 36
    else:          return 22, 30


# ── Agent ─────────────────────────────────────────────────────────────────────

class MeanReversionAgent(BaseAgent):
    name = "meanrev"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        closes = [c["close"] for c in candles if c.get("close")]
        if len(closes) < 25:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for mean-reversion")

        idx   = len(closes) - 1
        price = closes[idx]
        win   = closes[-20:]
        mean  = sum(win) / len(win)
        var   = sum((x - mean) ** 2 for x in win) / len(win)
        sd    = math.sqrt(var)
        if sd <= 1e-9:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Flat series — no dispersion to revert to")

        z      = (price - mean) / sd
        bb_pct = (price - (mean - 2 * sd)) / (4 * sd)
        rsi    = self._rsi(closes, 14)

        # ── Adaptive thresholds ───────────────────────────────────────────────
        hl               = _ou_half_life(closes)
        z_mild, z_strong = _z_thresholds(hl)
        rsi_s,  rsi_m    = _rsi_thresholds(hl)   # s=strong, m=mild (for oversold BUY)

        # If half-life > 60 the stock behaves like a trending instrument this
        # window — mean-reversion is unlikely to work; abstain cleanly.
        if hl > 60:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.35,
                reasoning=f"Trending (OU half-life {hl:.0f} bars) — mean-reversion edge absent.",
                indicators={"z_score": round(z, 2), "bb_pct": round(bb_pct, 2),
                            "rsi": round(rsi, 1), "half_life_bars": round(hl, 1)},
            )

        score = 0.0

        # ── Z-score signal (adaptive thresholds) ─────────────────────────────
        if z <= -z_strong:
            score += 0.45
        elif z <= -z_mild:
            score += 0.22
        if z >= z_strong:
            score -= 0.45
        elif z >= z_mild:
            score -= 0.22

        # ── RSI confirmation (adaptive thresholds) ────────────────────────────
        if rsi <= rsi_s:
            score += 0.25
        elif rsi <= rsi_m:
            score += 0.12
        if rsi >= (100 - rsi_s):
            score -= 0.25
        elif rsi >= (100 - rsi_m):
            score -= 0.12

        # ── Velocity damping — avoid falling-knife entries ────────────────────
        # If the move is still accelerating (|d1| > |d2|) in the same direction
        # as the z-score extreme, the reversal hasn't started yet — halve the
        # score rather than wait for a full bar flip.
        if idx >= 2:
            d1 = closes[idx]     - closes[idx - 1]   # most recent change
            d2 = closes[idx - 1] - closes[idx - 2]   # prior change
            still_accelerating = abs(d1) > abs(d2)
            if score > 0 and d1 < 0 and still_accelerating:
                score *= 0.5   # still falling fast — wait for deceleration
            elif score < 0 and d1 > 0 and still_accelerating:
                score *= 0.5   # still rising fast — wait

        # ── Stabilisation filter (bar-level) ─────────────────────────────────
        # Require at least one bar moving in the expected direction before acting.
        if idx >= 1:
            last_up = closes[idx] >= closes[idx - 1]
            if score > 0 and not last_up:
                score *= 0.6
            if score < 0 and last_up:
                score *= 0.6

        # ── Shared level map: a fade-the-drop entry is materially better when
        # the drop landed ON a tested support (the level provides the floor the
        # reversion thesis assumes). Levels come from the ensemble's shared
        # full-day map; ±0.30% of a 2+-touch support counts as "on it".
        at_support = None
        try:
            sup = [l for l in ((context or {}).get("levels") or {}).get("supports", [])
                   if l.get("touches", 1) >= 2 and l.get("dist_pct", 99) <= 0.30]
            if sup:
                at_support = sup[0]
        except Exception:
            at_support = None

        # ── Decision ─────────────────────────────────────────────────────────
        if score >= 0.30:
            action = "BUY"
            conf   = min(0.9, 0.5 + score)
            reason = (f"Mean-reversion BUY: z {z:.1f} (thresh ±{z_mild:.1f}/{z_strong:.1f}), "
                      f"RSI {rsi:.0f}, HL {hl:.0f}b — fade the drop.")
            if at_support:
                conf = min(0.92, conf + 0.08)
                reason += (f" On {at_support['touches']}x-tested support "
                           f"₹{at_support['price']:.2f} — floor confirmed.")
        elif score <= -0.30:
            action = "SELL"
            conf   = min(0.9, 0.5 + abs(score))
            reason = (f"Mean-reversion SELL: z {z:.1f} (thresh ±{z_mild:.1f}/{z_strong:.1f}), "
                      f"RSI {rsi:.0f}, HL {hl:.0f}b — fade the spike.")
        else:
            action = "HOLD"
            conf   = 0.4
            reason = (f"No reversion edge: z {z:.1f}, HL {hl:.0f}b "
                      f"(need |z| > {z_mild:.1f}).")

        return AgentSignal(
            agent_name=self.name, action=action,
            confidence=round(conf, 3), reasoning=reason,
            indicators={
                "z_score":        round(z, 2),
                "bb_pct":         round(bb_pct, 2),
                "rsi":            round(rsi, 1),
                "half_life_bars": round(hl, 1),
                "z_thresh_mild":  round(z_mild, 1),
                "z_thresh_strong":round(z_strong, 1),
                "rsi_thresh":     rsi_m,
            },
        )

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float:
        """Wilder's EMA-based RSI — same formula as TechnicalAgent."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_g  = sum(gains[:period])  / period
        avg_l  = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i])  / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        return 100.0 if avg_l == 0 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))
