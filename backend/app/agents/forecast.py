"""Monte-Carlo path forecaster — a CPU-frugal stand-in for heavyweight sequence
models (e.g. Kronos), built to capture the *useful* properties of such models
without GPUs, model weights, or training.

Instead of a transformer, it **resamples each symbol's OWN recent return
structure** (block bootstrap) to simulate many plausible future price paths,
then reads off:

  • a projected price path with an uncertainty band   → probabilistic sampling
  • expected favourable / adverse excursion           → data-driven target / stop
    (uses simulated intra-bar highs/lows, not just closes → projected highs/lows)
  • P(up) and P(target-before-stop) over the horizon  → path-level forecast

Why this honours the four "pros" of a big model, frugally:
  - Projected highs/lows for target/stop: simulated candle highs/lows give the
    expected favourable/adverse excursion, i.e. where to place TP / SL.
  - "Fine-tuned to NSE": it only ever samples THIS symbol's own recent history,
    so it auto-adapts per name and per regime — no training run needed.
  - Probabilistic sampling: hundreds of paths give an honest uncertainty band.
  - Beyond a linear fingerprint: block bootstrap preserves short-range
    autocorrelation and fat tails, so it is non-linear & distribution-free —
    far richer than a linear classifier, at numpy speed.

It is intentionally cheap (vectorised numpy, ~a millisecond for 400 paths), so
it is run only on low-volume decision paths (committed / delivery / a session's
single symbol), never across the full 1,800-symbol intraday sweep.
"""
from __future__ import annotations
import os

import numpy as np

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_N_PATHS  = int(os.getenv("FORECAST_N_PATHS", "400"))
_HORIZON  = int(os.getenv("FORECAST_HORIZON", "5"))     # bars ahead (days for daily candles)
_BLOCK    = int(os.getenv("FORECAST_BLOCK", "3"))       # bootstrap block length (autocorr capture)
_LOOKBACK = int(os.getenv("FORECAST_LOOKBACK", "120"))  # recent window the sim samples from
_MIN_HIST = 30


def _seed_from(closes: np.ndarray) -> int:
    """Deterministic seed so the same candle window yields a reproducible forecast."""
    tail = closes[-5:]
    return int(abs(float(np.sum(tail * 1000))) ) & 0x7FFFFFFF


class PathForecaster:
    def __init__(self, n_paths: int = _N_PATHS, horizon: int = _HORIZON, block: int = _BLOCK):
        self.n_paths = max(50, n_paths)
        self.horizon = max(1, horizon)
        self.block   = max(1, block)

    def forecast(self, candles: list[dict], horizon: int | None = None,
                 target_pct: float | None = None, stop_pct: float | None = None) -> dict:
        """Simulate future paths for the latest situation in `candles`.

        target_pct / stop_pct (in %) fix the TP / SL levels for the
        P(target-before-stop) calc; if omitted they default to the simulated
        typical favourable / adverse excursion.
        """
        H = max(1, int(horizon or self.horizon))
        closes = np.array([c.get("close") for c in candles
                           if c.get("close") not in (None, 0)], dtype=float)
        if closes.size < _MIN_HIST:
            return {"ok": False, "reason": "not enough history"}

        last = float(closes[-1])
        window = closes[-min(closes.size, _LOOKBACK):]
        rets = np.diff(np.log(window))
        if rets.size < self.block + 2 or not np.isfinite(rets).all():
            return {"ok": False, "reason": "insufficient/invalid returns"}

        # Typical intra-bar amplitude (high-low / close) → lets simulated candles
        # have realistic highs/lows, so target/stop reflect reach, not just closes.
        amp = self._intrabar_amp(candles, last)

        rng = np.random.default_rng(_seed_from(closes))
        # Block bootstrap: stitch random blocks of consecutive returns to length H.
        n_blocks = int(np.ceil(H / self.block))
        max_start = rets.size - self.block + 1
        starts = rng.integers(0, max_start, size=(self.n_paths, n_blocks))
        idx = starts[..., None] + np.arange(self.block)               # (N, n_blocks, block)
        sampled = rets[idx].reshape(self.n_paths, n_blocks * self.block)[:, :H]   # (N, H)

        log_paths = np.cumsum(sampled, axis=1)
        close_paths = last * np.exp(log_paths)                        # (N, H)
        hi_paths = close_paths * (1.0 + 0.5 * amp)
        lo_paths = close_paths * (1.0 - 0.5 * amp)

        final_ret = close_paths[:, -1] / last - 1.0
        mfe = hi_paths.max(axis=1) / last - 1.0                       # max favourable excursion
        mae = lo_paths.min(axis=1) / last - 1.0                       # max adverse excursion (<=0)

        p_up    = float((final_ret > 0).mean())
        exp_ret = float(np.median(final_ret))

        tgt = (target_pct / 100.0) if target_pct else float(np.median(mfe))
        stp = (stop_pct   / 100.0) if stop_pct   else float(-np.median(mae))
        tgt = max(tgt, 0.005)
        stp = max(stp, 0.005)

        up_lvl = last * (1.0 + tgt)
        dn_lvl = last * (1.0 - stp)
        # First-touch: does a path hit the target before the stop?
        t_touch = hi_paths >= up_lvl
        s_touch = lo_paths <= dn_lvl
        first_t = np.where(t_touch.any(1), t_touch.argmax(1), H + 1)
        first_s = np.where(s_touch.any(1), s_touch.argmax(1), H + 1)
        reached = (first_t <= H)
        p_target_first = float(((first_t < first_s) & reached).mean())

        return {
            "ok": True,
            "horizon": H,
            "n_paths": self.n_paths,
            "last_price": round(last, 2),
            "p_up": round(p_up, 3),
            "exp_return_pct": round(exp_ret * 100, 2),
            "target_pct": round(tgt * 100, 2),
            "stop_pct": round(stp * 100, 2),
            "target_price": round(up_lvl, 2),
            "stop_price": round(dn_lvl, 2),
            "rr": round(tgt / stp, 2) if stp > 0 else None,
            "p_target_before_stop": round(p_target_first, 3),
            "band_pct": {
                "p10": round(float(np.percentile(final_ret, 10)) * 100, 2),
                "p50": round(float(np.percentile(final_ret, 50)) * 100, 2),
                "p90": round(float(np.percentile(final_ret, 90)) * 100, 2),
            },
        }

    @staticmethod
    def _intrabar_amp(candles: list[dict], last: float) -> float:
        amps = []
        for c in candles[-_LOOKBACK:]:
            h, l, cl = c.get("high"), c.get("low"), c.get("close")
            if h and l and cl:
                amps.append((float(h) - float(l)) / float(cl))
        if not amps:
            return 0.01
        return float(np.clip(np.median(amps), 0.002, 0.15))


_forecaster: PathForecaster | None = None


def get_path_forecaster() -> PathForecaster:
    global _forecaster
    if _forecaster is None:
        _forecaster = PathForecaster()
    return _forecaster
