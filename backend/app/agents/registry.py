"""AI model registry — turn each independent model on/off and weight its vote.

The ensemble is a set of independent models (technical, pattern, momentum,
volatility, sentiment, RL, memory, and the newer mean-reversion / regime /
anomaly / gradient-boosted models). This registry lets each one be enabled or
disabled and given a manual vote-weight override at runtime, persisted in Redis,
without touching code or restarting.

It layers ON TOP of the LearningSystem: when no manual weight override is set,
the ensemble keeps using the learned/default weight; an override just pins it.
"""
from __future__ import annotations
import json
import time

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_KEY = "ai_engine:model_registry"

# Known models and their shipped defaults. `weight=None` → use the learned/default
# ensemble weight; a number pins a manual override. New models ship enabled with a
# sensible starting weight (kept modest until they prove themselves).
DEFAULTS: dict[str, dict] = {
    "technical":  {"enabled": True, "weight": None},
    "pattern":    {"enabled": True, "weight": None},
    "momentum":   {"enabled": True, "weight": None},
    "volatility": {"enabled": True, "weight": None},
    "sentiment":  {"enabled": True, "weight": None},
    "rl":         {"enabled": True, "weight": None},
    "memory":     {"enabled": True, "weight": None},
    # ── new independent models ───────────────────────────────────────────────
    "meanrev":       {"enabled": True, "weight": None},
    "regime":        {"enabled": True, "weight": None},
    "anomaly":       {"enabled": True, "weight": None},
    "gbm":           {"enabled": True, "weight": None},
    "day_structure": {"enabled": True, "weight": None},
}

# Human-facing metadata for the control panel.
META: dict[str, dict] = {
    "technical":  {"label": "Technical", "kind": "rule", "desc": "RSI/MACD/VWAP/MA structure."},
    "pattern":    {"label": "Pattern (candles)", "kind": "rule", "desc": "Candlestick pattern agent."},
    "momentum":   {"label": "Momentum", "kind": "rule", "desc": "ROC, volume surge, acceleration."},
    "volatility": {"label": "Volatility / Risk", "kind": "rule", "desc": "ATR/regime risk score; can force HOLD."},
    "sentiment":  {"label": "Sentiment", "kind": "data", "desc": "News/sentiment tilt."},
    "rl":         {"label": "Reinforcement Learner", "kind": "learned", "desc": "Q-table policy."},
    "memory":     {"label": "Pattern Memory", "kind": "learned", "desc": "k-NN win-rate over past cases; evidence gate."},
    "meanrev":    {"label": "Mean-Reversion", "kind": "rule", "desc": "Fades overextended moves (Bollinger/z-score/RSI extremes)."},
    "regime":     {"label": "Market-Regime Filter", "kind": "model", "desc": "Trend/chop/high-vol; reweights momentum vs mean-reversion."},
    "anomaly":    {"label": "Anomaly / Trap Detector", "kind": "model", "desc": "IsolationForest flags abnormal bars; vetoes risky entries."},
    "gbm":        {"label": "Gradient-Boosted P(up)", "kind": "learned", "desc": "Non-linear ML over the pattern fingerprint; learned, trainable."},
    "day_structure": {"label": "Day Structure", "kind": "rule", "desc": "Intraday S/R levels, day-range position, R/R ratio — avoids buying near day highs."},
}

_cache: dict = {"data": None, "ts": 0.0}
_TTL = 8.0


def _merge(stored: dict) -> dict:
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for name, cfg in (stored or {}).items():
        if name not in out:
            out[name] = {"enabled": True, "weight": None}
        if "enabled" in cfg:
            out[name]["enabled"] = bool(cfg["enabled"])
        if "weight" in cfg:
            w = cfg["weight"]
            out[name]["weight"] = (float(w) if w is not None else None)
    return out


async def get_registry(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["data"] is not None and (now - _cache["ts"]) < _TTL:
        return _cache["data"]
    stored = {}
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_KEY)
        if raw:
            stored = json.loads(raw)
    except Exception as exc:
        logger.debug("registry load failed: %s", exc)
    data = _merge(stored)
    _cache.update({"data": data, "ts": now})
    return data


async def set_model(name: str, enabled: bool | None = None, weight: float | None = ...) -> dict:
    """Update one model's enabled flag and/or weight override. Pass weight=None to
    clear the override (revert to learned/default); omit weight to leave it."""
    from app.utils.redis_client import cache_get, cache_set
    try:
        raw = await cache_get(_KEY)
        stored = json.loads(raw) if raw else {}
    except Exception:
        stored = {}
    cur = stored.get(name, {})
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if weight is not ...:
        cur["weight"] = (float(weight) if weight is not None else None)
    stored[name] = cur
    try:
        await cache_set(_KEY, json.dumps(stored), expire=86400 * 30)
    except Exception as exc:
        logger.warning("registry save failed: %s", exc)
    _cache["ts"] = 0.0  # invalidate
    return await get_registry(force=True)


def is_enabled(reg: dict, name: str) -> bool:
    return bool(reg.get(name, {}).get("enabled", True))


def weight_override(reg: dict, name: str):
    return reg.get(name, {}).get("weight")
