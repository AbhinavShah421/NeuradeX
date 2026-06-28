"""Market-Regime Filter — Gaussian HMM for robust regime classification.

v2 changes from the original rule-based classifier:
  • GaussianHMM (4 states) fitted on a rolling feature window via Baum-Welch EM
  • Viterbi decoding — transition probabilities mean a single outlier bar cannot
    flip the regime; the sequence context matters
  • State→label mapping from learned emission means, no hardcoded thresholds
  • Fixed ADX: uses Wilder's smoothed ADX instead of single-period DX (noisy)
  • Falls back to rule-based when < _HMM_MIN_OBS bars or EM fails

Observation features (4 per bar, z-scored over the window):
  [0] atr_pct     — normalized true range as % of price  (volatility proxy)
  [1] adx         — Wilder's smoothed ADX                (trend strength)
  [2] slope_abs   — |10-bar SMA slope| as % per 10 bars  (direction strength)
  [3] vol_ratio   — volume vs 10-bar EMA                 (activity level)
"""
from __future__ import annotations
import math
import numpy as np

from .base import AgentSignal, BaseAgent
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_HMM_MIN_OBS = 60   # minimum bars to attempt HMM (need enough for EM stability)
_N_STATES    = 4


# ── Pure-numpy diagonal-covariance Gaussian HMM ───────────────────────────────

class _GaussHMM:
    """Minimal 4-state Gaussian HMM. No external dependency beyond numpy.

    Fitted by Baum-Welch EM; decoded by Viterbi. Diagonal covariance keeps
    the number of parameters manageable for short series (60-200 bars).
    """

    def __init__(self, n_states: int = 4, n_iter: int = 40, tol: float = 1e-3):
        self.K      = n_states
        self.n_iter = n_iter
        self.tol    = tol
        self.fitted = False
        self.mu:     np.ndarray | None = None
        self.sigma:  np.ndarray | None = None
        self.pi:     np.ndarray | None = None
        self.A:      np.ndarray | None = None

    # ── fitting ───────────────────────────────────────────────────────────────

    def fit(self, X: np.ndarray) -> "_GaussHMM":
        """Baum-Welch EM on observation matrix X (T, D)."""
        T, D = X.shape
        K    = self.K
        rng  = np.random.default_rng(42)

        # k-means++ seeding for initial means
        idx0 = int(rng.integers(T))
        centers: list[np.ndarray] = [X[idx0].copy()]
        for _ in range(K - 1):
            dists = np.array([min(float(np.sum((x - c) ** 2)) for c in centers)
                              for x in X])
            probs = dists / (dists.sum() + 1e-300)
            centers.append(X[rng.choice(T, p=probs)].copy())

        self.mu    = np.array(centers, dtype=np.float64)
        self.sigma = np.tile(np.var(X, axis=0) + 1e-4, (K, 1)).astype(np.float64)
        # Bias transition matrix toward self-loops so regimes are sticky
        self.A     = np.full((K, K), 0.05 / (K - 1), dtype=np.float64)
        np.fill_diagonal(self.A, 0.95)
        self.pi    = np.full(K, 1.0 / K, dtype=np.float64)

        prev_ll = -np.inf
        for iteration in range(self.n_iter):
            log_b           = self._log_b(X)            # (T, K)
            alpha, scales   = self._forward(log_b)      # (T, K), (T,)
            beta            = self._backward(log_b, scales)  # (T, K)

            # Posterior state probs (gamma)
            gamma  = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True) + 1e-300

            # Joint consecutive-state probs (xi)  shape (T-1, K, K)
            log_b_next = log_b[1:]                      # (T-1, K)
            xi = (alpha[:-1, :, None] *
                  self.A[None, :, :] *
                  np.exp(log_b_next[:, None, :]) *
                  beta[1:, None, :])
            xi_norm = xi.sum(axis=(1, 2), keepdims=True) + 1e-300
            xi /= xi_norm

            # M-step
            self.pi = gamma[0] + 1e-300
            self.pi /= self.pi.sum()

            self.A = xi.sum(axis=0) + 1e-300
            self.A /= self.A.sum(axis=1, keepdims=True)

            gs         = gamma.sum(axis=0)              # (K,)
            self.mu    = (gamma.T @ X) / (gs[:, None] + 1e-300)
            diff       = X[:, None, :] - self.mu[None, :, :]  # (T, K, D)
            self.sigma = ((gamma[:, :, None] * diff ** 2).sum(axis=0) /
                          (gs[:, None] + 1e-300)) + 1e-4

            ll = float(np.log(scales + 1e-300).sum())
            if iteration > 2 and abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll

        self.fitted = True
        return self

    # ── decoding ──────────────────────────────────────────────────────────────

    def decode(self, X: np.ndarray) -> np.ndarray:
        """Viterbi; returns state index per time step (T,)."""
        T  = X.shape[0]
        K  = self.K
        lb = self._log_b(X)
        la = np.log(self.A + 1e-300)

        delta = np.full((T, K), -np.inf)
        psi   = np.zeros((T, K), dtype=int)
        delta[0] = np.log(self.pi + 1e-300) + lb[0]

        for t in range(1, T):
            trans    = delta[t - 1, :, None] + la   # (K, K)
            psi[t]   = trans.argmax(axis=0)
            delta[t] = trans.max(axis=0) + lb[t]

        states     = np.empty(T, dtype=int)
        states[-1] = int(delta[-1].argmax())
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]
        return states

    # ── state labelling ───────────────────────────────────────────────────────

    def label_states(self) -> dict[int, str]:
        """Map state indices to regime names via emission means.
        Feature order: [atr_pct, adx, slope_abs, vol_ratio]  (all z-scored)
        """
        K   = self.K
        mu  = self.mu   # (K, D)
        atr_col, adx_col, slope_col = 0, 1, 2
        used: set[int] = set()
        labels: dict[int, str] = {}

        # Highest ATR → high_vol
        hv = max(range(K), key=lambda k: float(mu[k, atr_col]))
        labels[hv] = "high_vol"; used.add(hv)

        # Among remainder: highest combined ADX + slope → trend
        rem = [k for k in range(K) if k not in used]
        tr  = max(rem, key=lambda k: float(mu[k, adx_col]) + float(mu[k, slope_col]))
        labels[tr] = "trend"; used.add(tr)

        # Lowest ATR → range (quiet sideways)
        rem2 = [k for k in range(K) if k not in used]
        rg   = min(rem2, key=lambda k: float(mu[k, atr_col]))
        labels[rg] = "range"; used.add(rg)

        # Remaining → chop (directionless noise)
        ch = next(k for k in range(K) if k not in used)
        labels[ch] = "chop"
        return labels

    # ── internals ─────────────────────────────────────────────────────────────

    def _log_b(self, X: np.ndarray) -> np.ndarray:
        """Log Gaussian emission probability for each (t, k). (T, K)"""
        T, D  = X.shape
        out   = np.zeros((T, self.K), dtype=np.float64)
        for k in range(self.K):
            diff       = X - self.mu[k]
            out[:, k]  = -0.5 * (np.sum(diff ** 2 / self.sigma[k], axis=1) +
                                  np.sum(np.log(2 * math.pi * self.sigma[k])))
        return out

    def _forward(self, log_b: np.ndarray):
        T, K   = log_b.shape
        alpha  = np.zeros((T, K), dtype=np.float64)
        scales = np.zeros(T, dtype=np.float64)
        raw        = self.pi * np.exp(log_b[0] - log_b[0].max())
        scales[0]  = raw.sum() + 1e-300
        alpha[0]   = raw / scales[0]
        for t in range(1, T):
            raw      = (alpha[t - 1] @ self.A) * np.exp(log_b[t] - log_b[t].max())
            scales[t] = raw.sum() + 1e-300
            alpha[t]  = raw / scales[t]
        return alpha, scales

    def _backward(self, log_b: np.ndarray, scales: np.ndarray) -> np.ndarray:
        T, K  = log_b.shape
        beta  = np.zeros((T, K), dtype=np.float64)
        beta[-1] = 1.0
        for t in range(T - 2, -1, -1):
            raw     = (self.A * np.exp(log_b[t + 1] - log_b[t + 1].max())) @ beta[t + 1]
            beta[t] = raw / (scales[t + 1] + 1e-300)
        return beta


# ── Feature engineering ───────────────────────────────────────────────────────

def _feature_matrix(closes: list, highs: list, lows: list, volumes: list) -> np.ndarray:
    """Returns (T-1, 4) z-scored observation matrix for the HMM.
    One row per inter-bar transition; row i corresponds to candle i+1.
    """
    ca = np.array(closes,  dtype=np.float64)
    ha = np.array(highs,   dtype=np.float64)
    la = np.array(lows,    dtype=np.float64)
    va = np.array(volumes, dtype=np.float64)
    n  = len(ca)

    # True Range
    tr = np.maximum.reduce([
        ha[1:] - la[1:],
        np.abs(ha[1:] - ca[:-1]),
        np.abs(la[1:] - ca[:-1]),
    ])

    # Wilder's 14-period ATR (proper smoothing, not SMA)
    atr14    = _wilder(tr, 14)
    atr_pct  = atr14 / (ca[1:] + 1e-9) * 100      # (n-1,)

    # Wilder's ADX (smoothed DX)
    up  = ha[1:] - ha[:-1]
    dn  = la[:-1] - la[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr14  = _wilder(tr,  14)
    pdm14 = _wilder(pdm, 14)
    mdm14 = _wilder(mdm, 14)
    pdi   = 100.0 * pdm14 / (tr14 + 1e-9)
    mdi   = 100.0 * mdm14 / (tr14 + 1e-9)
    denom = pdi + mdi
    dx    = np.where(denom > 1e-9, 100.0 * np.abs(pdi - mdi) / denom, 0.0)
    adx   = _wilder(dx, 14)                         # (n-1,)

    # |10-bar SMA slope| as % change per 10 bars
    slope = np.zeros(n - 1, dtype=np.float64)
    for i in range(10, n):
        prev_sma = ca[i - 10:i].mean()
        curr_sma = ca[i - 9:i + 1].mean()
        if prev_sma > 1e-9:
            slope[i - 1] = abs((curr_sma - prev_sma) / prev_sma) * 100

    # Volume ratio vs 10-bar EMA
    vol_ema  = _ema(va, 10)                         # (n,)
    vol_rat  = va / (vol_ema + 1e-9)
    vol_rat  = vol_rat[1:]                          # (n-1,)

    X = np.column_stack([atr_pct, adx, slope, vol_rat])   # (n-1, 4)

    # Z-score: centres features around 0 so EM distances are comparable
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return (X - mu) / sd


def _wilder(series: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing: initial = SMA of first `period` bars, then EMA-like."""
    n   = len(series)
    out = np.zeros(n, dtype=np.float64)
    if n < period:
        return out
    out[period - 1] = series[:period].mean()
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + series[i]) / period
    return out


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    out = np.zeros(len(series), dtype=np.float64)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


# ── The Agent ─────────────────────────────────────────────────────────────────

class RegimeFilterAgent(BaseAgent):
    name = "regime"

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        closes  = [c["close"] for c in candles if c.get("close")]
        highs   = [c.get("high",   c["close"]) for c in candles if c.get("close")]
        lows    = [c.get("low",    c["close"]) for c in candles if c.get("close")]
        volumes = [c.get("volume", 1)          for c in candles if c.get("close")]

        if len(closes) < 30:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.3,
                               reasoning="Insufficient data for regime classification",
                               indicators={"regime": "unknown"})

        regime = "chop"
        method = "rules"

        # ── HMM path (preferred when enough data) ────────────────────────────
        if len(closes) >= _HMM_MIN_OBS:
            try:
                X = _feature_matrix(closes, highs, lows, volumes)
                if X.shape[0] >= _HMM_MIN_OBS - 1 and np.isfinite(X).all():
                    hmm      = _GaussHMM(n_states=_N_STATES).fit(X)
                    states   = hmm.decode(X)
                    lab_map  = hmm.label_states()
                    regime   = lab_map.get(int(states[-1]), "chop")
                    method   = "hmm"
            except Exception as exc:
                logger.debug("HMM regime failed (%s), using rules", exc)

        # ── Rule-based fallback (or when HMM unavailable) ────────────────────
        if method == "rules":
            regime = self._rule_classify(closes, highs, lows)

        # Indicator values for the UI and for the ensemble's _apply_regime()
        price    = closes[-1]
        adx_val  = self._adx_smoothed(highs, lows, closes, 14)
        atr_val  = self._atr(highs, lows, closes, 14)
        atr_pct  = (atr_val / price * 100) if price else 0.0
        sma20    = sum(closes[-20:]) / 20
        sma50    = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20

        sig = {
            "regime":     regime,
            "method":     method,
            "adx":        round(adx_val, 1),
            "atr_pct":    round(atr_pct, 2),
            "ma_aligned": (price > sma20 > sma50) or (price < sma20 < sma50),
        }

        # Light directional vote only in a clear trend; HOLD otherwise
        if regime == "trend" and price > sma20:
            action = "BUY"
            conf   = min(0.80, 0.50 + adx_val / 100)
            reason = f"Trend regime ({method}, ADX {adx_val:.0f}) — upward, momentum favoured."
        elif regime == "trend" and price < sma20:
            action = "SELL"
            conf   = min(0.80, 0.50 + adx_val / 100)
            reason = f"Trend regime ({method}, ADX {adx_val:.0f}) — downward, momentum favoured."
        else:
            action = "HOLD"
            conf   = 0.45
            reason = f"{regime.replace('_', ' ').title()} regime ({method}, ADX {adx_val:.0f}, ATR {atr_pct:.1f}%)."

        return AgentSignal(agent_name=self.name, action=action,
                           confidence=round(conf, 3), reasoning=reason,
                           indicators=sig)

    # ── Rule-based fallback ───────────────────────────────────────────────────

    def _rule_classify(self, closes: list, highs: list, lows: list) -> str:
        price   = closes[-1]
        adx     = self._adx_smoothed(highs, lows, closes, 14)
        atr     = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / price * 100) if price else 0.0
        sma20   = sum(closes[-20:]) / 20
        sma50   = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
        ma_aligned = (price > sma20 > sma50) or (price < sma20 < sma50)
        prev_sma20 = sum(closes[-30:-10]) / 20 if len(closes) >= 30 else sma20
        slope_pct  = (sma20 - prev_sma20) / prev_sma20 * 100 if prev_sma20 else 0.0

        if atr_pct >= 4.0:
            return "high_vol"
        if adx >= 25 and ma_aligned and abs(slope_pct) >= 0.15:
            return "trend"
        if adx < 18 and atr_pct < 1.5:
            return "range"
        return "chop"

    # ── Indicator helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
        if len(closes) < 2:
            return 0.0
        trs = [max(highs[i] - lows[i],
                   abs(highs[i] - closes[i - 1]),
                   abs(lows[i]  - closes[i - 1]))
               for i in range(1, len(closes))]
        return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.0

    @staticmethod
    def _adx_smoothed(highs: list, lows: list, closes: list, period: int = 14) -> float:
        # kept here for clarity — also used by quick_regime() below
        """Wilder's smoothed ADX — replaces the old noisy single-period DX."""
        n = len(closes)
        if n < period + 2:
            return 0.0
        ha = np.array(highs,  dtype=np.float64)
        la = np.array(lows,   dtype=np.float64)
        ca = np.array(closes, dtype=np.float64)
        tr  = np.maximum.reduce([ha[1:] - la[1:],
                                  np.abs(ha[1:] - ca[:-1]),
                                  np.abs(la[1:] - ca[:-1])])
        up  = ha[1:] - ha[:-1]
        dn  = la[:-1] - la[1:]
        pdm = np.where((up > dn) & (up > 0), up, 0.0)
        mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
        tr14  = _wilder(tr,  period)
        pdm14 = _wilder(pdm, period)
        mdm14 = _wilder(mdm, period)
        pdi   = 100.0 * pdm14 / (tr14 + 1e-9)
        mdi   = 100.0 * mdm14 / (tr14 + 1e-9)
        denom = pdi + mdi
        dx    = np.where(denom > 1e-9, 100.0 * np.abs(pdi - mdi) / denom, 0.0)
        adx   = _wilder(dx, period)
        return float(adx[-1]) if adx[-1] > 0 else 0.0


# ── Standalone helper (no HMM) ────────────────────────────────────────────────

def quick_regime(candles: list[dict]) -> str:
    """Fast rule-based regime label for agents that need regime context inline.

    Skips the HMM (which needs ≥60 bars and EM fitting) and uses the same
    rule-based classifier as the HMM fallback — adequate for a gating signal.
    Returns one of: "trend", "chop", "range", "high_vol", "unknown".
    """
    closes  = [c["close"] for c in candles if c.get("close")]
    highs   = [c.get("high",  c["close"]) for c in candles if c.get("close")]
    lows    = [c.get("low",   c["close"]) for c in candles if c.get("close")]
    if len(closes) < 30:
        return "unknown"
    price    = closes[-1]
    atr      = RegimeFilterAgent._atr(highs, lows, closes, 14)
    adx      = RegimeFilterAgent._adx_smoothed(highs, lows, closes, 14)
    atr_pct  = (atr / price * 100) if price else 0.0
    sma20    = sum(closes[-20:]) / 20
    sma50    = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    ma_aligned = (price > sma20 > sma50) or (price < sma20 < sma50)
    prev_sma20 = sum(closes[-30:-10]) / 20 if len(closes) >= 30 else sma20
    slope_pct  = (sma20 - prev_sma20) / prev_sma20 * 100 if prev_sma20 else 0.0
    if atr_pct >= 4.0:
        return "high_vol"
    if adx >= 25 and ma_aligned and abs(slope_pct) >= 0.15:
        return "trend"
    if adx < 18 and atr_pct < 1.5:
        return "range"
    return "chop"
