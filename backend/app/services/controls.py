"""Runtime control plane for the entry gate and the ensemble vote.

Every knob that decides whether a trade happens used to live in one of three
places, none of them adjustable without a deploy: module constants read at
import (`_DIR_DOMINANCE`), literals inside the `TRADE_GATES` presets
(`score_min`), or environment variables. This module puts a single Redis-backed
override layer in front of all of them, so a value can be changed and take
effect on the next candle.

Three things make that safe enough to expose in a UI:

  1. **Bounds.** Every control declares a valid range and is rejected outside
     it. `score_min` is the sharp edge — the gate compares a 0-100 entry score
     against it, so a typo of 7.8 for 78 does not tighten anything, it opens
     the floodgates on the very next bar. Bounds are enforced here, server
     side, not in the form.
  2. **Defaults travel with the control.** The shipped value is read FROM the
     code that owns it rather than copied, so a default cannot silently drift
     out of sync, and "reset" always restores what actually ships.
  3. **Evidence travels with the control.** Several of these have been measured
     and the answer was "do not touch this". A page that lets you change a knob
     without telling you it was already tested is worse than no page — so the
     measured result is attached to the control and shown where the change is
     made.

Reads are cached for _CACHE_TTL seconds because the ensemble runs per candle
per symbol; an uncached Redis read per decision would be thousands per minute.
The cost is that a change takes up to that long to bite, which is the same
trade-off the existing trade-gate selector already makes.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_KEY = "ai_engine:runtime_controls"
_CACHE_TTL = 10.0
_cache: dict = {"data": None, "ts": 0.0}


def _gate_defaults() -> dict:
    """Shipped gate presets, read from the module that owns them."""
    try:
        from app.services.sessions_service import TRADE_GATES
        return TRADE_GATES
    except Exception:
        logger.warning("could not read TRADE_GATES for defaults", exc_info=True)
        return {}


def _ensemble_defaults() -> dict:
    try:
        from app.agents import ensemble as e
        return {
            "vote_mode": e._VOTE_MODE,
            "dir_dominance": e._DIR_DOMINANCE,
            "dir_min_voters": e._DIR_MIN_VOTERS,
            "mem_min_samples": e._MEM_MIN_SAMPLES,
            "mem_gate_winrate": e._MEM_GATE_WINRATE,
            "mem_strong_winrate": e._MEM_STRONG_WINRATE,
        }
    except Exception:
        logger.warning("could not read ensemble defaults", exc_info=True)
        return {}


# ── Control specifications ───────────────────────────────────────────────────
# `evidence` is not decoration. Where a knob has been measured, the finding is
# stated at the point of change; several of these are documented dead ends and
# the page should say so rather than inviting a re-run.

def control_specs() -> list[dict]:
    g = _gate_defaults()
    e = _ensemble_defaults()
    out: list[dict] = []

    for mode in ("strict", "gentle", "loose"):
        p = g.get(mode) or {}
        if not p:
            continue
        out += [
            {"id": f"gate.{mode}.score_min", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Minimum entry score", "type": "number", "min": 40, "max": 100, "step": 1,
             "default": p.get("score_min"),
             "read_in": "sessions_service.py — score vs gate['score_min']",
             "help": "The 0-100 entry-quality score a setup must reach. Full marks are ~95-100; "
                     "one 8-13 point shortfall still clears 78, two do not.",
             "danger": "Lowering this is the fastest way to trade far more, and worse. "
                       "A value in the single digits admits essentially everything."},
            {"id": f"gate.{mode}.min_buy", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Minimum BUY voters", "type": "number", "min": 1, "max": 6, "step": 1,
             "default": p.get("min_buy"),
             "read_in": "sessions_service.py — buy_votes >= min_buy",
             "help": "How many agents must vote BUY for full consensus points.",
             "evidence": "BUY-vote count predicts win rate: 1 -> 21%, 2 -> 41%, 3 -> 50%. "
                         "The panel tops out around 2-3, so 2 is the practical floor."},
            {"id": f"gate.{mode}.min_conf", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Confidence floor", "type": "number", "min": 0.0, "max": 1.0, "step": 0.01,
             "default": p.get("min_conf"), "read_in": "sessions_service.py",
             "help": "Ensemble confidence below this is skipped."},
            {"id": f"gate.{mode}.max_conf", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Confidence ceiling", "type": "number", "min": 0.0, "max": 1.05, "step": 0.01,
             "default": p.get("max_conf"), "read_in": "sessions_service.py",
             "help": "Entries ABOVE this are also skipped — deliberately.",
             "evidence": "Confidence is anti-predictive above ~0.70 over 7k+ intraday trades: "
                         "win rate 40% at 0.50-0.60 falls to 16% above 0.90. Raising the ceiling "
                         "buys the worst cell."},
            {"id": f"gate.{mode}.override_ceiling", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Override ceiling", "type": "number", "min": 0.0, "max": 1.05, "step": 0.01,
             "default": p.get("override_ceiling"), "read_in": "sessions_service.py",
             "help": "When the ensemble's winner is not BUY but it is this confident, do not "
                     "override it into a long. The worst losers are confident-HOLD overrides."},
            {"id": f"gate.{mode}.need_reliable", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Require a proven BUY co-signer", "type": "boolean",
             "default": p.get("need_reliable"), "read_in": "sessions_service.py",
             "help": "Require one of gbm / meanrev / memory / day_structure to vote BUY.",
             "evidence": "Measured 2026-09-04: those four DO rank top on day-clustered BUY edge, "
                         "and requiring one looked worth +0.022pp (t=2.75) in sample — but it "
                         "INVERTED on a held-out period (-0.005, t=-0.59). Treat as unproven."},
            {"id": f"gate.{mode}.min_grade", "group": f"Entry gate — {p.get('label', mode)}",
             "label": "Minimum pattern grade", "type": "enum", "options": ["A", "B", "C", "D"],
             "default": p.get("min_grade"), "read_in": "sessions_service.py — pattern quality gate",
             "help": "Pattern-engine grade a setup must reach."},
        ]

    out += [
        {"id": "ensemble.vote_mode", "group": "Ensemble vote", "label": "Vote mode",
         "type": "enum", "options": ["directional", "legacy"], "default": e.get("vote_mode"),
         "read_in": "agents/ensemble.py — _VOTE_MODE",
         "help": "'directional' makes entry a BUY-vs-SELL contest with HOLD as abstention. "
                 "'legacy' restores max-vote.",
         "danger": "Legacy max-vote let abstaining HOLD agents decide — BUY won 0 of 2,554 "
                   "decisions on 2026-07-06. This is why directional exists."},
        {"id": "ensemble.dir_dominance", "group": "Ensemble vote", "label": "Dominance margin",
         "type": "number", "min": 1.0, "max": 3.0, "step": 0.05, "default": e.get("dir_dominance"),
         "read_in": "agents/ensemble.py — _DIR_DOMINANCE",
         "help": "The winning side's weighted mass must beat the loser's by this multiple.",
         "evidence": "1.3 with 2+ voters was calibrated on CF-labelled bars. Reshaping the "
                     "aggregation has been A/B'd twice and never beat what ships."},
        {"id": "ensemble.dir_min_voters", "group": "Ensemble vote", "label": "Minimum voters a side needs",
         "type": "number", "min": 1, "max": 5, "step": 1, "default": e.get("dir_min_voters"),
         "read_in": "agents/ensemble.py — _DIR_MIN_VOTERS",
         "help": "A direction needs this many distinct agents before it can win.",
         "danger": "Setting this to 1 lets a single flooding agent decide alone — gbm voted "
                   "SELL on 96% of bars, which is the case this rule exists to stop."},
        {"id": "ensemble.mem_min_samples", "group": "Pattern-memory gate",
         "label": "Minimum precedent cases", "type": "number", "min": 0, "max": 50, "step": 1,
         "default": e.get("mem_min_samples"), "read_in": "agents/ensemble.py — _MEM_MIN_SAMPLES",
         "help": "How many similar past cases the memory bank needs before it may veto."},
        {"id": "ensemble.mem_gate_winrate", "group": "Pattern-memory gate",
         "label": "Veto below this win rate", "type": "number", "min": 0.0, "max": 1.0, "step": 0.01,
         "default": e.get("mem_gate_winrate"), "read_in": "agents/ensemble.py — _MEM_GATE_WINRATE",
         "help": "Precedent below this win rate vetoes the trade to HOLD."},
        {"id": "ensemble.mem_strong_winrate", "group": "Pattern-memory gate",
         "label": "Boost above this win rate", "type": "number", "min": 0.0, "max": 1.0, "step": 0.01,
         "default": e.get("mem_strong_winrate"), "read_in": "agents/ensemble.py — _MEM_STRONG_WINRATE",
         "help": "Precedent above this actively boosts confidence."},
    ]
    return out


_SPEC_BY_ID: dict[str, dict] = {}


def spec(control_id: str) -> Optional[dict]:
    global _SPEC_BY_ID
    if not _SPEC_BY_ID:
        _SPEC_BY_ID = {c["id"]: c for c in control_specs()}
    return _SPEC_BY_ID.get(control_id)


def validate(control_id: str, value: Any) -> tuple[bool, Any, str]:
    """Coerce and bounds-check. Returns (ok, coerced_value, error)."""
    s = spec(control_id)
    if not s:
        return False, None, f"unknown control {control_id}"
    t = s.get("type")
    try:
        if t == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            return True, bool(value), ""
        if t == "enum":
            v = str(value)
            if v not in (s.get("options") or []):
                return False, None, f"must be one of {s.get('options')}"
            return True, v, ""
        if t == "number":
            v = float(value)
            lo, hi = s.get("min"), s.get("max")
            if lo is not None and v < lo:
                return False, None, f"below minimum {lo}"
            if hi is not None and v > hi:
                return False, None, f"above maximum {hi}"
            # Integer controls must stay integers — a step of 1 with a value of
            # 2.5 would silently truncate somewhere downstream.
            if s.get("step") == 1:
                if abs(v - round(v)) > 1e-9:
                    return False, None, "must be a whole number"
                return True, int(round(v)), ""
            return True, v, ""
    except (TypeError, ValueError):
        return False, None, "not a valid value"
    return False, None, f"unsupported type {t}"


async def _load() -> dict:
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]
    data: dict = {}
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_KEY)
        if raw:
            data = json.loads(raw)
    except Exception:
        # A cache blip must not silently revert live trading behaviour, so keep
        # serving the last good overrides rather than falling back to defaults.
        logger.debug("controls read failed; keeping last known", exc_info=True)
        return _cache["data"] or {}
    _cache["data"], _cache["ts"] = data, now
    return data


async def overrides() -> dict:
    """Only the values that differ from what ships."""
    return dict(await _load())


async def get(control_id: str, default: Any = None) -> Any:
    ov = await _load()
    if control_id in ov:
        return ov[control_id]
    s = spec(control_id)
    return s.get("default") if s else default


async def set_value(control_id: str, value: Any) -> tuple[bool, str]:
    ok, coerced, err = validate(control_id, value)
    if not ok:
        return False, err
    ov = dict(await _load())
    s = spec(control_id) or {}
    if coerced == s.get("default"):
        ov.pop(control_id, None)          # back to shipped = no override stored
    else:
        ov[control_id] = coerced
    return await _persist(ov, f"set {control_id}={coerced}")


async def reset(control_id: Optional[str] = None) -> tuple[bool, str]:
    """Drop one override, or all of them."""
    ov = {} if control_id is None else {k: v for k, v in (await _load()).items()
                                        if k != control_id}
    return await _persist(ov, "reset all" if control_id is None else f"reset {control_id}")


async def _persist(ov: dict, what: str) -> tuple[bool, str]:
    try:
        from app.utils.redis_client import cache_set
        await cache_set(_KEY, json.dumps(ov))
        _cache["data"], _cache["ts"] = ov, time.monotonic()
        logger.info("runtime control changed: %s", what,
                    extra={"log_type": "ai_engine", "event": "control_changed",
                           "detail": what, "active_overrides": len(ov)})
        return True, ""
    except Exception as exc:
        logger.warning("control persist failed: %s", exc)
        return False, str(exc)[:200]


# ── Consumer helpers ─────────────────────────────────────────────────────────

async def gate_overrides(mode: str) -> dict:
    """Overrides for one gate preset, as a dict ready to merge over it."""
    ov = await _load()
    prefix = f"gate.{mode}."
    return {k[len(prefix):]: v for k, v in ov.items() if k.startswith(prefix)}


async def ensemble_setting(name: str) -> Any:
    """Effective value of one ensemble knob, override or shipped default."""
    return await get(f"ensemble.{name}")


async def snapshot() -> dict:
    """Everything the control page needs in one call.

    `active_gate` matters more than it looks. Three gate presets are listed but
    only ONE is in force, and a page that shows all three identically invites
    the mistake of carefully tuning Strict while the runner reads Gentle. The
    mode lives in Redis under the session service's own key, so it is read from
    there rather than mirrored here.
    """
    ov = await _load()
    specs = control_specs()
    for c in specs:
        c["value"] = ov.get(c["id"], c.get("default"))
        c["overridden"] = c["id"] in ov
        # The UI groups the gate presets into tabs; carrying the mode on the
        # control saves it parsing the id or the group label to find it.
        if c["id"].startswith("gate."):
            c["gate_mode"] = c["id"].split(".")[1]

    try:
        from app.services.sessions_service import get_trade_gate
        active_gate = await get_trade_gate()
    except Exception:
        logger.warning("could not read the active trade gate", exc_info=True)
        active_gate = None

    return {
        "controls": specs,
        "active_gate": active_gate,
        "override_count": len(ov),
        "cache_ttl_secs": _CACHE_TTL,
        "note": ("Changes apply within the cache TTL — on the next candle in practice. "
                 "Overrides live in Redis and survive a restart; 'reset' restores the "
                 "value the code ships."),
    }
