"""Pre-execution trade validator — the last checkpoint before a BUY is real.

**What this is, and deliberately is not.**

It is not a second opinion on whether the trade is a good idea. That was tried:
the 8B reviewer's verdict tracked how the prompt was framed rather than the
precedent it was handed, which is why it does not get a vote in the ensemble.
Adding a model that *judges* here would launder prompt sensitivity into an
execution decision.

So this verifies FACTS instead. Every check answers a question with a knowable
answer — is this price real, does this decision actually satisfy the gate it
claims to have passed, are we inside our own risk limits — and every one of them
can only BLOCK. Nothing here can turn a HOLD into a BUY. That asymmetry is the
whole safety argument: a bug in this file can cost a trade, never cause one.

**Why a separate checkpoint when `_step` already has a veto chain.**

The chain in sessions_service is nine `if enter:` blocks of strategy judgement —
day structure, tested ceilings, pattern grade, cooldowns. Those encode opinions
about which setups are worth taking. This file encodes the invariants that must
hold no matter what the strategy thinks, and it runs LAST, so it also catches
the case where an earlier block was edited and got its own logic wrong.

Two failures from this project's own history are what it is built around:

  * `entry_price` of 0. Ninety trade records were stored with a zero entry
    price because a field name did not match across a service boundary. Nothing
    errored. A trade priced at zero is not a trade, and it must never reach the
    executor.
  * The forming-candle veto. A zero-volume in-progress candle made the anomaly
    agent veto roughly half of all decisions for two days. The same shape of
    bad input — a bar that is not finished — must be recognised, not silently
    traded on.

Checks return `None` when they pass and a string reason when they block. The
string goes straight into the decision's `reason`, so a blocked entry explains
itself in the session log without anyone reading this file.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# Confidence above this is measured ANTI-predictive on this universe: win rate
# 40% in the 0.50-0.60 band falls to 16% above 0.90 over 7k+ intraday trades.
# The gate already carries a max_conf ceiling; this is the backstop for a gate
# that has been widened by hand on the Trading Controls page.
_ANTI_PREDICTIVE_CONF = 0.90

# A bar with no volume at all has not traded. Treating one as a real candle is
# what produced the 2026-07-13 no-trade days from the other direction.
_MIN_BAR_VOLUME = 0


class Verdict:
    """Outcome of a validation pass.

    `ok` is only true when every check passed. `reasons` carries every failure,
    not just the first, because when a decision is wrong it is usually wrong in
    more than one way and the log is more useful with all of them.
    """

    __slots__ = ("ok", "reasons", "checked")

    def __init__(self, reasons: list[str], checked: int):
        self.ok = not reasons
        self.reasons = reasons
        self.checked = checked

    def __bool__(self) -> bool:          # `if verdict:` reads naturally
        return self.ok

    def as_reason(self) -> str:
        return "; ".join(self.reasons)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checks": self.checked, "reasons": list(self.reasons)}


# ── Individual checks ────────────────────────────────────────────────────────
# Each takes the context dict and returns None (pass) or a reason (block).

def _check_price(ctx: dict) -> Optional[str]:
    """A tradable price is positive and finite.

    Zero is the specific value that has actually occurred here, but NaN is the
    one that would slip furthest — it compares false against every threshold, so
    a NaN price passes a `< limit` gate silently.
    """
    price = ctx.get("price")
    if price is None:
        return "no entry price on the decision"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return f"entry price is not a number ({price!r})"
    if math.isnan(p) or math.isinf(p):
        return f"entry price is not finite ({p})"
    if p <= 0:
        return f"entry price is {p} — a trade cannot be priced at or below zero"
    return None


def _check_bar_complete(ctx: dict) -> Optional[str]:
    """The decision bar must be a bar that actually traded.

    An in-progress candle reports zero volume and a close that is really just
    the last tick. Entering on one means entering on a price that has not been
    tested by any counterparty.
    """
    candle = ctx.get("candle") or {}
    if not candle:
        return None                      # nothing to verify against
    vol = candle.get("volume")
    if vol is None:
        return None                      # volume genuinely unavailable, not zero
    try:
        v = float(vol)
    except (TypeError, ValueError):
        return None
    if v <= _MIN_BAR_VOLUME:
        return (f"decision bar {candle.get('time', '?')} has zero volume — "
                f"an in-progress candle is not a tradable price")
    return None


def _check_gate_consistency(ctx: dict) -> Optional[str]:
    """The decision must actually satisfy the gate it claims to have passed.

    This is the check that catches an edited veto chain. `enter` is computed
    from a long conjunction; if a future change drops a term, the score can fall
    under the threshold while `enter` stays true, and nothing else would notice.
    """
    gate = ctx.get("gate") or {}
    if not gate:
        return None
    score, score_min = ctx.get("score"), gate.get("score_min")
    if score is not None and score_min is not None and score < score_min:
        return (f"entry score {score:.0f} is below the {gate.get('label', '?')} gate's "
                f"minimum {score_min} — the gate was not actually cleared")

    conf = ctx.get("confidence")
    if conf is not None:
        lo, hi = gate.get("min_conf"), gate.get("max_conf")
        if lo is not None and conf < lo:
            return f"confidence {conf:.0%} is below the gate floor {lo:.0%}"
        if hi is not None and conf > hi:
            return f"confidence {conf:.0%} is above the gate ceiling {hi:.0%}"

    if gate.get("require_buy"):
        votes, need = ctx.get("buy_votes"), gate.get("min_buy")
        if votes is not None and need is not None and votes < need:
            return (f"{votes} BUY vote(s) against the gate's required {need} — "
                    f"this gate requires a BUY consensus")
    return None


def _anti_predictive_threshold() -> float:
    """The band, honouring a runtime override from Trading Controls.

    Read per call rather than cached: the controls layer already caches for
    10s, and a validator that holds a stale threshold would silently ignore the
    operator's change for as long as the process lives.
    """
    try:
        from app.services.controls import get_sync
        v = get_sync("validator.anti_predictive_conf")
        if v is not None:
            return float(v)
    except Exception:
        pass
    return _ANTI_PREDICTIVE_CONF


def _check_confidence_band(ctx: dict) -> Optional[str]:
    """Backstop against a hand-widened confidence ceiling.

    The Trading Controls page can raise `max_conf` to 1.05. That is allowed —
    it is a measured knob and the operator may want to test it — but the band
    above 0.90 is where the win rate is 16%, so entering there should be an
    explicit act rather than a side effect of loosening a gate.
    """
    band = _anti_predictive_threshold()
    conf = ctx.get("confidence")
    if conf is None or conf < band:
        return None
    if ctx.get("allow_anti_predictive"):
        return None
    return (f"confidence {conf:.0%} sits in the measured anti-predictive band "
            f"(≥{band:.0%} wins ~16% vs ~40% at 0.50-0.60)")


def _check_not_already_long(ctx: dict) -> Optional[str]:
    """One position per session. A second entry on top of an open one doubles
    the size the risk sizing computed for a single position."""
    if (ctx.get("position_status") or "").upper() == "LONG":
        return "already holding a LONG in this session — refusing to stack a second entry"
    return None


def _check_symbol(ctx: dict) -> Optional[str]:
    """A symbol that the feed cannot price is not tradable.

    Renamed and delisted tickers 403 on the broker's historical endpoint, and a
    hardcoded universe goes stale silently; fifteen such symbols were found in
    one sweep. If the caller has resolved a tradable set, honour it.
    """
    symbol = (ctx.get("symbol") or "").upper()
    if not symbol:
        return "decision has no symbol"
    universe = ctx.get("tradable_universe")
    if universe and symbol not in universe:
        return f"{symbol} is not in the tradable universe — possibly renamed or delisted"
    return None


def _check_risk_limits(ctx: dict) -> Optional[str]:
    """Session-level risk stops, checked at the point of no return.

    `daily_loss_limit` is the operator's own number; breaching it should stop
    new risk regardless of how good the next setup looks.
    """
    limit = ctx.get("daily_loss_limit")
    realised = ctx.get("day_pnl")
    if limit and realised is not None and realised <= -abs(limit):
        return (f"day P&L {realised:+.2f} has reached the {abs(limit):.2f} loss limit — "
                f"no new entries today")

    cap = ctx.get("max_positions")
    open_now = ctx.get("open_positions")
    if cap and open_now is not None and open_now >= cap:
        return f"{open_now} positions already open against a cap of {cap}"
    return None


def _check_market_open(ctx: dict) -> Optional[str]:
    """Live and paper entries need a live market. Backtest and replay do not —
    they are replaying a day that is already over, which is the point."""
    mode = (ctx.get("mode") or "").lower()
    if mode in ("backtest", "replay"):
        return None
    if ctx.get("market_open") is False:
        return "market is closed — no live entry"
    return None




# ─────────────────────────────────────────────────────────────────────────────
# Independent recomputation
#
# The checks above verify that the numbers handed to us are self-consistent.
# This does something stronger: it rebuilds the entry score from the RAW inputs
# — the agent votes, the indicators, the candle — and compares the result with
# what `_step` claimed.
#
# The distinction matters. `_step` accumulates `score` across ~120 lines and
# nine mutation points, then gates on a conjunction of eight terms. A future
# edit that double-counts a component, drops a branch, or flips a comparison
# produces a wrong score that is still perfectly self-consistent with itself,
# and every consistency check would pass. Only a second implementation reading
# the same inputs can catch that.
#
# The scoring CONSTANTS are imported rather than duplicated — which agents are
# reliable, which sellers are discounted. Copying those would create a
# maintenance trap that fires false alarms every time a list is deliberately
# edited. What is reimplemented here is the ARITHMETIC, which is where the
# drift actually happens.
#
# Weights, from the 2026-07-14 scored gate: consensus 30, reliable co-sign 10,
# trend 25, RSI timing 25, ensemble stance 10, +5 timing bonus, capped at 100.
# ─────────────────────────────────────────────────────────────────────────────

# A recomputed score is allowed to differ from the claimed one by this much
# before it is treated as a disagreement. Both sides do float arithmetic on the
# same integers, so anything above rounding noise is a real divergence.
_SCORE_TOLERANCE = 0.51


def recompute_score(ctx: dict) -> Optional[dict]:
    """Rebuild the entry score from raw inputs. None when inputs are absent.

    Returns `{"score", "hard_block", "components"}`. `components` is kept so a
    disagreement can say WHICH part diverged rather than only that the totals
    differ — a mismatch of exactly 13 points names the VWAP leg on sight.
    """
    agents = ctx.get("agents")
    ind = ctx.get("indicators")
    candle = ctx.get("candle") or {}
    gate = ctx.get("gate") or {}
    if agents is None or ind is None or not candle:
        return None                      # not enough to recompute; checks above still ran

    try:
        from app.services.sessions_service import (
            _DISCOUNTED_SELLERS, _RELIABLE_BUY_AGENTS, _TREND_FILTER_LEGACY,
        )
    except Exception as exc:
        logger.debug("recompute unavailable — cannot import scoring constants: %s", exc)
        return None

    comp: dict[str, float] = {}
    hard_block = False

    buy_voters = {a.get("agent_name") for a in agents if a.get("action") == "BUY"}
    buy_voters.discard(None)
    # `_step` may drop a cold-start memory vote from the count; honour the count
    # it derived rather than recomputing that carve-out, since it depends on the
    # memory agent's own case count.
    buy_votes = ctx.get("buy_votes")
    if buy_votes is None:
        buy_votes = len(buy_voters)
    has_reliable_buy = bool(buy_voters & _RELIABLE_BUY_AGENTS)
    sell_voters_n = sum(1 for a in agents if a.get("action") == "SELL"
                        and a.get("agent_name") not in _DISCOUNTED_SELLERS)
    net_consensus = buy_votes - sell_voters_n

    min_buy = gate.get("min_buy", 2)
    reliable_single = gate.get("reliable_single", False)

    # ── Consensus (max 30) ──────────────────────────────────────────────────
    if buy_votes >= min_buy:
        comp["consensus"] = 30.0 if net_consensus >= 2 else (15.0 if net_consensus == 1 else 12.0)
    elif reliable_single and has_reliable_buy:
        comp["consensus"] = 14.0
    else:
        comp["consensus"] = 8.0

    # ── Reliable co-sign (max 10) ───────────────────────────────────────────
    comp["reliable_cosign"] = 10.0 if has_reliable_buy else 0.0

    # ── Trend (max 25) ──────────────────────────────────────────────────────
    # The direction of this test was inverted 2026-08-10: points now go to
    # WEAKNESS and the both-legs-up cell is the hard block, because chasing
    # strength measured as the worst cell (22.6% win vs 28.6%). The legacy
    # branch is kept because NEURADEX_TREND_FILTER=legacy is the documented
    # one-step rollback.
    price_now = candle.get("close", 0.0)
    below_vwap = price_now < ind.get("vwap", price_now)
    sma_down = ind.get("sma5", 0) < ind.get("sma20", 0)
    if _TREND_FILTER_LEGACY:
        vwap_ok = not below_vwap
        sma_ok = ind.get("sma5", 0) >= ind.get("sma20", 0)
        if not (vwap_ok or sma_ok):
            hard_block = True
            comp["trend"] = 0.0
        else:
            comp["trend"] = (13.0 if vwap_ok else 0.0) + (12.0 if sma_ok else 0.0)
    else:
        if not (below_vwap or sma_down):
            hard_block = True
            comp["trend"] = 0.0
        else:
            comp["trend"] = (13.0 if below_vwap else 0.0) + (12.0 if sma_down else 0.0)

    # ── RSI timing (max 25) ─────────────────────────────────────────────────
    # 20 unconditional + 5 for the oversold-below-VWAP cell. Only 5 of the 25
    # are conditional deliberately: the effect is mostly the same "weakness
    # beats strength" phenomenon as the trend leg, and a full RSI ladder would
    # double-count it.
    rsi_now = ind.get("rsi", 50.0)
    comp["rsi"] = 20.0 + (5.0 if (rsi_now < 45 and below_vwap) else 0.0)

    # ── Ensemble stance (max 10) ────────────────────────────────────────────
    ens_action = ctx.get("ens_action")
    comp["ensemble"] = 10.0 if ens_action == "BUY" else (5.0 if ens_action == "HOLD" else 0.0)

    total = sum(comp.values())
    # ── Timing bonus (+5, capped) ───────────────────────────────────────────
    if ctx.get("tsig") == 1:
        total = min(100.0, total + 5.0)
        comp["timing_bonus"] = 5.0

    return {"score": total, "hard_block": hard_block, "components": comp}


def _check_recomputed_score(ctx: dict) -> Optional[str]:
    """The claimed score must survive being derived a second time.

    A disagreement means `_step` and this module read the same inputs and
    reached different conclusions. One of them is wrong, and which one is not
    knowable from here — so the trade does not go.
    """
    claimed = ctx.get("score")
    if claimed is None:
        return None
    got = recompute_score(ctx)
    if got is None:
        return None                      # inputs unavailable; not a failure

    if got["hard_block"]:
        return ("recomputed from raw inputs this setup is a HARD BLOCK "
                "(trend filter) yet the decision reached entry")

    delta = abs(got["score"] - float(claimed))
    if delta > _SCORE_TOLERANCE:
        parts = ", ".join(f"{k} {v:.0f}" for k, v in sorted(got["components"].items()))
        return (f"score disagreement: decision says {float(claimed):.0f}, "
                f"independent recomputation says {got['score']:.0f} "
                f"(delta {delta:.0f}; recomputed as {parts}) — "
                f"one of the two is wrong, so the trade does not go")

    # The score can agree and still sit under the gate, if score_min was raised
    # between the decision and here.
    score_min = (ctx.get("gate") or {}).get("score_min")
    if score_min is not None and got["score"] < score_min:
        return (f"recomputed score {got['score']:.0f} is under the gate minimum "
                f"{score_min}")
    return None


# Order matters only for readability of the log; every check runs regardless.
CHECKS: list[tuple[str, Callable[[dict], Optional[str]]]] = [
    ("price",        _check_price),
    ("bar",          _check_bar_complete),
    ("symbol",       _check_symbol),
    ("gate",         _check_gate_consistency),
    ("confidence",   _check_confidence_band),
    ("position",     _check_not_already_long),
    ("risk",         _check_risk_limits),
    ("market",       _check_market_open),
    # Last, because it is the expensive one and the cheap facts above
    # should fail first when they are going to fail.
    ("recompute",    _check_recomputed_score),
]


def validate_entry(ctx: dict) -> Verdict:
    """Run every check against one proposed entry.

    A check that raises is treated as a BLOCK, not a pass. This validator exists
    to be the thing that is certain; a check too broken to answer cannot be
    allowed to wave a trade through by accident.
    """
    reasons: list[str] = []
    for name, check in CHECKS:
        try:
            reason = check(ctx)
        except Exception as exc:
            logger.warning("validator check %s raised: %s", name, exc, exc_info=True)
            reason = f"validation check '{name}' failed to run ({exc}) — refusing to assume it passed"
        if reason:
            reasons.append(reason)

    verdict = Verdict(reasons, len(CHECKS))
    if not verdict.ok:
        logger.info(
            "entry blocked by validator: %s %s — %s",
            ctx.get("symbol", "?"), ctx.get("candle", {}).get("time", "?"), verdict.as_reason(),
            extra={"log_type": "ai_engine", "event": "validator_block",
                   "symbol": ctx.get("symbol"), "reasons": verdict.reasons},
        )
    return verdict


def build_context(*, symbol: str, candle: dict, gate: dict, score: float,
                  confidence: float, buy_votes: int, position_status: str,
                  mode: str, session: Optional[dict] = None,
                  agents: Optional[list] = None, indicators: Optional[dict] = None,
                  ens_action: Optional[str] = None, tsig: Optional[int] = None,
                  **extra: Any) -> dict:
    """Assemble the context from what `_step` already has in scope.

    Kept separate from `validate_entry` so the checks stay pure dicts-in,
    string-out and can be tested without constructing a session.
    """
    s = session or {}
    ctx = {
        "symbol": symbol,
        "candle": candle,
        "price": candle.get("close"),
        "gate": gate,
        "score": score,
        "confidence": confidence,
        "buy_votes": buy_votes,
        "position_status": position_status,
        "mode": mode,
        "day_pnl": s.get("day_pnl"),
        "daily_loss_limit": s.get("daily_loss_limit"),
        "max_positions": s.get("max_positions"),
        "open_positions": s.get("open_positions"),
        "market_open": s.get("market_open"),
        "tradable_universe": s.get("tradable_universe"),
        # Raw inputs for the independent recomputation. Without these the
        # recompute check abstains rather than guessing.
        "agents": agents,
        "indicators": indicators,
        "ens_action": ens_action,
        "tsig": tsig,
    }
    ctx.update(extra)
    return ctx
