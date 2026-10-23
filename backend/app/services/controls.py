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


def _validator_defaults() -> dict:
    try:
        from app.agents import validator as v
        return {"anti_predictive_conf": v._ANTI_PREDICTIVE_CONF}
    except Exception:
        logger.warning("could not read validator defaults", exc_info=True)
        return {}


def _scanner_defaults() -> dict:
    """Shipped scanner-worker thresholds.

    The scanner is a SEPARATE service, so these cannot be imported — the values
    are mirrored here and the scanner reads the override layer from the same
    Redis key. That mirroring is the one place this module cannot honour its own
    "defaults travel with the control" rule, so the numbers are commented with
    their source and a drift between the two shows up as a control whose reset
    does not match the code.
    """
    return {
        "sector_min_names": 4,      # workers/sectors.py _MIN_NAMES
        "sector_broad": 0.65,       # workers/sectors.py _BROAD
        "gap_material": 1.5,        # workers/movers.py _GAP_MATERIAL
        "vol_conviction": 2.0,      # workers/movers.py _VOL_CONVICTION
        "vol_thin": 0.8,            # workers/movers.py _VOL_THIN
        "rsi_extended": 72.0,       # workers/movers.py _RSI_EXTENDED
    }


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
    v = _validator_defaults()
    sc = _scanner_defaults()
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

        # ── Entry validator ─────────────────────────────────────────────────
        # The last checkpoint before a BUY. Only one of its checks has a number
        # worth turning: the rest test facts (is the price finite, did the bar
        # trade) that have no threshold to tune.
        {"id": "validator.anti_predictive_conf", "group": "Entry validator",
         "label": "Anti-predictive confidence band", "type": "number",
         "min": 0.50, "max": 1.05, "step": 0.01, "default": v.get("anti_predictive_conf"),
         "read_in": "agents/validator.py — _ANTI_PREDICTIVE_CONF",
         "help": "Entries at or above this confidence are blocked even when the gate "
                 "allows them, unless the caller passes allow_anti_predictive.",
         "evidence": "Win rate is 40% in the 0.50-0.60 band and 16% above 0.90 over "
                     "7k+ intraday trades. This is the backstop for a gate whose "
                     "ceiling has been widened by hand on this page."},

        # ── Scanner: what counts as a sector move ───────────────────────────
        {"id": "scanner.sector_min_names", "group": "Scanner — sectors",
         "label": "Minimum names to read a sector", "type": "number",
         "min": 2, "max": 20, "step": 1, "default": sc.get("sector_min_names"),
         "read_in": "workers/sectors.py — _MIN_NAMES",
         "help": "Below this a sector is reported but never ranked.",
         "danger": "Setting this to 2-3 lets one stock BE its sector: its move "
                   "becomes a 'sector move' wearing a label."},
        {"id": "scanner.sector_broad", "group": "Scanner — sectors",
         "label": "Breadth that counts as broad", "type": "number",
         "min": 0.50, "max": 0.95, "step": 0.01, "default": sc.get("sector_broad"),
         "read_in": "workers/sectors.py — _BROAD",
         "help": "Share of a sector moving the same way before it is called a "
                 "supporting tailwind for a name inside it."},

        # ── Scanner: what counts as a tradable move ─────────────────────────
        {"id": "scanner.gap_material", "group": "Scanner — movers",
         "label": "Gap that dominates the day", "type": "number",
         "min": 0.5, "max": 5.0, "step": 0.1, "default": sc.get("gap_material"),
         "read_in": "workers/movers.py — _GAP_MATERIAL",
         "help": "Opening gap above this is treated as the leading fact about the "
                 "move, and a move that is mostly gap is classed 'already happened'."},
        {"id": "scanner.vol_conviction", "group": "Scanner — movers",
         "label": "Relative volume = real participation", "type": "number",
         "min": 1.0, "max": 6.0, "step": 0.1, "default": sc.get("vol_conviction"),
         "read_in": "workers/movers.py — _VOL_CONVICTION",
         "help": "Above this a move is classed 'in progress' — the only class a "
                 "promotion may be drawn from.",
         "danger": "Lowering this widens what can be promoted. It is the main "
                   "control on how many nominations reach the reviewer."},
        {"id": "scanner.vol_thin", "group": "Scanner — movers",
         "label": "Relative volume = thin", "type": "number",
         "min": 0.2, "max": 1.0, "step": 0.05, "default": sc.get("vol_thin"),
         "read_in": "workers/movers.py — _VOL_THIN",
         "help": "Below this a move is nobody trading, and unwinds as easily as "
                 "it formed."},
        {"id": "scanner.rsi_extended", "group": "Scanner — movers",
         "label": "RSI that marks a move late", "type": "number",
         "min": 60, "max": 90, "step": 1, "default": sc.get("rsi_extended"),
         "read_in": "workers/movers.py — _RSI_EXTENDED",
         "help": "Gainers at or above this are classed 'extended'.",
         "evidence": "This RSI is computed on DAILY candles, so it does NOT catch "
                     "an intraday rip — names up 6-13% on the day read 54-68. The "
                     "buying-strength constraint tests the trend legs instead."},
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


def get_sync(control_id: str) -> Any:
    """An override, from the in-process cache only. None when not overridden.

    For synchronous callers — the validator's checks are plain functions that
    cannot await. It deliberately does NOT reach Redis: a blocking network call
    inside a per-candle decision path is how a gate check becomes a latency
    problem. The async path refreshes the cache every _CACHE_TTL anyway, so this
    is at most that far behind, and a cold cache simply returns None and the
    caller keeps the value the code ships.
    """
    data = _cache.get("data")
    return data.get(control_id) if data else None


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
