"""Per-session post-mortem: why each losing trade lost, and which agent pushed it.

Two questions, deliberately answered by two different mechanisms, because only
one of them can be answered from a single session:

  1. WHAT HAPPENED — for each closed trade: the setup at entry, every agent's
     vote and weight at that moment, and the mechanical exit that booked the
     loss. This is a factual record of one session. It needs no statistics and
     it makes no claim.

  2. WHO IS CULPABLE — whether an agent systematically pushes BUY into losers.
     This CANNOT come from one session (1-5 trades), and it must not come from
     executed trades at all.

The second point is the whole reason this module exists rather than a SQL view.
`trade_records.agent_signals` only exists for entries the gate already allowed,
and the `gentle` gate *requires* a pattern-or-sentiment co-sign — so any
per-agent ranking computed on executed trades is conditioned on the very rule
being examined. Measured 2026-08-10, that framing produced a striking and
entirely false split (gbm +0.309 vs sentiment -0.396 on 74 executed trades)
which collapsed to t = -0.44 once recomputed on the unbiased corpus. A UI that
fingers an agent from one session's executed trades would reproduce that error
every single session, with confidence.

So culpability is computed on `session_decisions` — executed AND rejected, so
no gate selection — as a base-rate-corrected lift:

    culpability = P(agent voted BUY | decision lost) - P(agent voted BUY | won)

The control is what makes the number mean anything. An agent that votes BUY on
everything has a high BUY-rate among losers *and* among winners, so its lift is
~0: loud, not culpable. Positive lift = pushes BUY disproportionately into
losers. Negative lift = the agent's BUY genuinely discriminates, i.e. it is
protective. Averaged per day and t-tested across days, because entries within a
day are massively correlated and entry-level t-stats on this data are worthless
(a headline t=+17.5 once collapsed to +0.03 under day-clustering).

Measured 2026-09-04 over 43 days: no agent clears Bonferroni as a culprit
(`technical` is highest at +0.014, t=1.83). Three clear it as PROTECTIVE —
memory (t=-3.48), meanrev (-3.15), gbm (-2.93). The honest answer to "which
agent is the culprit" is currently "none of them, and here is the evidence",
which is a far more useful thing for the panel to say than a name.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta, timezone
from typing import Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_BASELINE_KEY = "ai_engine:agent_culpability_baseline"
_BASELINE_TTL = 12 * 3600

# A day must contribute at least this many losing and winning decisions for that
# agent before the day counts, and an agent needs this many qualifying days
# before any lift is reported.
_MIN_PER_CLASS_PER_DAY = 30
_MIN_DAYS = 10

# 12 agents are tested at once, so the single-test 1.96 is the wrong bar.
# Bonferroni at alpha=0.05 over 12 tests lands near |t| = 3.0.
_N_AGENTS_TESTED = 12
_BONFERRONI_T = 3.0


# ── Setup classification ─────────────────────────────────────────────────────
# Deterministic, over the six entry indicators persisted on every decision
# (rsi, sma5, sma20, vwap, momentum_pct, atr). This replaced an 8B classifier
# that, given the identical job, put 74% of trades in a single bucket; the rule
# version spreads the corpus 41/17/16/13/4.5/3.6/2.5/2.3%. The taxonomy was
# never the problem. A decision tree over six numbers does not need an LLM, and
# a rule cannot leak the outcome into its own label.
SETUP_TAGS: tuple[str, ...] = (
    "momentum_into_resistance", "strength_drift", "overbought_entry",
    "oversold_bounce", "range_breakout", "pullback_in_trend",
    "low_volatility_drift", "mean_reversion_fade", "no_clear_setup",
)


def classify_setup(ind: Optional[dict], price: Optional[float]) -> str:
    """Tag the setup from entry-time indicators only. Never sees the outcome."""
    ind = ind or {}
    try:
        rsi = ind.get("rsi")
        s5, s20 = ind.get("sma5"), ind.get("sma20")
        vwap = ind.get("vwap")
        mom = float(ind.get("momentum_pct") or 0.0)
        atr = float(ind.get("atr") or 0.0)
        if None in (rsi, s5, s20, vwap) or not price:
            return "no_clear_setup"
        if rsi > 70:
            return "overbought_entry"
        if rsi < 30:
            return "oversold_bounce"
        if s5 > s20 and price > vwap:
            # Both halves of the "buying strength" cell — price above VWAP with
            # an up SMA cross — which is the one grouping measured to matter
            # (-0.0815pp, t=-3.18, day-clustered over 41 days, 27.5% of
            # entries). The first cut of this taxonomy only had the high-
            # momentum half and let the rest fall through to `no_clear_setup`,
            # so the largest bucket in the corpus (41%) was quietly absorbing
            # the very cell the study had flagged as harmful. Splitting on
            # momentum keeps the measured subset intact and names the rest.
            return "momentum_into_resistance" if mom > 0.15 else "strength_drift"
        if s5 > s20 and price <= vwap:
            return "pullback_in_trend"
        if s5 <= s20 and price > vwap:
            return "range_breakout"
        if s5 <= s20 and mom < -0.15:
            return "mean_reversion_fade"
        if atr / price * 100.0 < 0.05:
            return "low_volatility_drift"
    except Exception:
        logger.debug("setup classification failed", exc_info=True)
    return "no_clear_setup"


# Day-clustered edge of each tag versus the same day's other entries, measured
# 2026-09-04 over 1,013,577 cf-labelled entries (mean per-day difference in pp,
# with the t across days). Shown beside a trade so the reader can see whether
# its setup sits in a known-bad cell.
#
# Read these as RELATIVE. Every absolute cell is negative and already net of the
# 0.125% round-trip cost, so this ranks degrees of loss, not good against good.
# Nine tags are tested, so Bonferroni wants |t| >= ~2.9.
SETUP_EDGE_PP: dict[str, float] = {
    "no_clear_setup": 0.058,          # t=+3.25 — the residual, once strength is named
    "low_volatility_drift": 0.027,    # t=+0.70
    "mean_reversion_fade": 0.027,     # t=+1.22
    "pullback_in_trend": 0.025,       # t=+1.18
    "oversold_bounce": 0.009,         # t=+0.31
    "overbought_entry": -0.024,       # t=-1.03
    "range_breakout": -0.056,         # t=-1.60
    "momentum_into_resistance": -0.069,  # t=-3.18
    "strength_drift": -0.072,         # t=-2.68, and 21.5% of all entries
}

# Tags whose day-clustered t clears Bonferroni. Only these are worth flagging in
# a UI; the rest are directional at best.
SETUP_ESTABLISHED: frozenset[str] = frozenset({
    "momentum_into_resistance", "strength_drift",
})

# Round-trip cost on this universe. Used as the threshold for "was there ever a
# real gain here" — a peak below it could never have been banked, so a trade
# that only reached it is an entry failure, not an exit failure.
_ROUND_TRIP_COST_PCT = 0.125


def _loss_reason(exit_reason: Optional[str], pnl_pct: Optional[float],
                 held_min: Optional[int]) -> str:
    """Plain-language mechanical cause. This is what the exit did, not a theory
    about what the entry should have been."""
    er = (exit_reason or "").lower()
    if not er or er == "unknown":
        return "exit reason not recorded"
    if "stop" in er:
        return "stopped out — price hit the stop before the target"
    if "stagnat" in er or "time" in er or "hold" in er:
        return (f"decayed into the {held_min or 60}-minute stagnation exit — "
                "never reached the target in either direction")
    if "squareoff" in er or "square_off" in er or "eod" in er:
        return "held to the forced end-of-day square-off"
    if "trail" in er or "lock" in er:
        return "trailing profit-lock exit"
    if "target" in er or "take" in er:
        return "take-profit target"
    if "rsi" in er or "momentum" in er or "fast" in er:
        return f"discretionary momentum/RSI exit ({er})"
    return er


async def agent_culpability_baseline(force: bool = False) -> dict:
    """Day-clustered per-agent BUY-lift over the whole cf-labelled corpus.

    Cached for 12h — it scans every labelled decision and its answer moves on
    the timescale of days, not requests.
    """
    from app.utils.redis_client import cache_get, cache_set
    if not force:
        try:
            raw = await cache_get(_BASELINE_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.debug("culpability baseline cache read failed", exc_info=True)

    from sqlalchemy import text
    from app.database.postgres import engine
    sql = text("""
        WITH x AS (
          SELECT COALESCE(sm.date,(sd.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) AS day,
                 a->>'agent' AS agent, a->>'action' AS act,
                 sd.cf_pnl_pct > 0 AS won
          FROM session_decisions sd
          LEFT JOIN session_metadata sm ON sm.session_id = sd.session_id
          CROSS JOIN LATERAL jsonb_array_elements(sd.agents) a
          WHERE sd.agents IS NOT NULL AND sd.cf_pnl_pct IS NOT NULL
        ), per_day AS (
          SELECT day, agent,
                 count(*) FILTER (WHERE NOT won) AS n_loss,
                 count(*) FILTER (WHERE won)     AS n_win,
                 count(*) FILTER (WHERE NOT won AND act='BUY')::float
                   / NULLIF(count(*) FILTER (WHERE NOT won),0) AS br_loss,
                 count(*) FILTER (WHERE won AND act='BUY')::float
                   / NULLIF(count(*) FILTER (WHERE won),0)     AS br_win
          FROM x GROUP BY day, agent
        ), d AS (
          SELECT agent, br_loss - br_win AS lift FROM per_day
          WHERE n_loss >= :mpc AND n_win >= :mpc
        )
        SELECT agent, count(*) AS days, avg(lift) AS mean_lift,
               avg(lift)/NULLIF(stddev_samp(lift)/sqrt(count(*)),0) AS t_stat,
               100.0*count(*) FILTER (WHERE lift > 0)/count(*) AS pct_pos
        FROM d GROUP BY agent HAVING count(*) >= :mdays
        ORDER BY avg(lift) DESC
    """)
    out: dict[str, Any] = {"agents": [], "computed_at": None}
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(
                sql, {"mpc": _MIN_PER_CLASS_PER_DAY, "mdays": _MIN_DAYS})).fetchall()
        from datetime import datetime, timezone
        for r in rows:
            t = float(r[3]) if r[3] is not None else None
            out["agents"].append({
                "agent": r[0], "days": int(r[1]),
                "lift": round(float(r[2]), 4),
                "t_stat": round(t, 2) if t is not None else None,
                "pct_days_positive": round(float(r[4]), 0),
                "verdict": _verdict(float(r[2]), t),
            })
        out["computed_at"] = datetime.now(timezone.utc).isoformat()
        out["method"] = ("P(BUY|loss) - P(BUY|win) per day on session_decisions "
                         "(executed AND rejected), t across days; "
                         f"Bonferroni over {_N_AGENTS_TESTED} agents needs |t| >= {_BONFERRONI_T}")
        try:
            await cache_set(_BASELINE_KEY, json.dumps(out), expire=_BASELINE_TTL)
        except Exception:
            logger.debug("culpability baseline cache write failed", exc_info=True)
    except Exception as exc:
        logger.warning("agent culpability baseline failed: %s", exc)
        out["error"] = str(exc)[:200]
    return out


_baseline_task: "Optional[asyncio.Task]" = None


def _refresh_baseline_in_background() -> None:
    """Kick off one baseline computation, at most one at a time.

    Without the guard, a page that renders several post-mortems would start a
    100-second full-corpus scan for each of them.
    """
    global _baseline_task
    if _baseline_task is not None and not _baseline_task.done():
        return
    try:
        _baseline_task = asyncio.create_task(agent_culpability_baseline())
    except RuntimeError:
        _baseline_task = None          # no running loop (sync caller) — skip


async def baseline_if_warm() -> dict:
    """The culpability baseline, but never at the cost of the request.

    The baseline is a GLOBAL statistic — identical for every trade — and
    computing it expands every cf-labelled decision into one row per agent vote
    (~1M decisions x ~12 agents) to cluster the lift by day, and it sat on the
    critical path of a UI click. Measured 2026-09-08: the failing request took
    107 seconds end to end, and the scan on its own timed at 169. nginx gives
    up at 90, so the first person to open a post-mortem after the 12-hour cache
    expired got `Request failed with status code 504` while the backend carried
    on and finished 17 seconds later. Every other request in that window served
    in under 1.1s — nothing was blocked or contended, the whole 107s was this
    one scan.

    Worse, a 504 does not just fail: the client disconnect can cancel the task
    before it writes the cache, so the next click pays the full cost again. Two
    504s twenty-two minutes apart is exactly what that looks like.

    So serve it warm or not at all, and refresh out of band. The post-mortem is
    complete without it — the baseline only decorates each agent with its
    long-run verdict, and the UI already renders nothing when that is absent.
    `culpability_baseline_loop` keeps it warm so the absent case stays rare.
    """
    from app.utils.redis_client import cache_get
    try:
        raw = await cache_get(_BASELINE_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("culpability baseline cache read failed", exc_info=True)
        return {"agents": [], "pending": False}
    _refresh_baseline_in_background()
    return {"agents": [], "pending": True}


async def culpability_baseline_loop() -> None:
    """Keep the baseline warm so no UI request ever computes it.

    Due at 05:00 IST — after counterfactual labelling (04:00) has supplied the
    cf_pnl_pct the scan reads, so the day's decisions are included rather than
    missed by an hour.
    """
    from app.utils.nightly import nightly_loop, NotReady

    async def _run() -> object:
        res = await agent_culpability_baseline(force=True)
        if res.get("error"):
            raise NotReady(f"baseline scan failed: {res['error']}")
        return {"agents": len(res.get("agents", []))}

    await nightly_loop("culpability_baseline", 5, _run,
                       label="Agent culpability baseline")


def _verdict(lift: float, t: Optional[float]) -> str:
    """Only a Bonferroni-clearing t earns a label. Everything else is 'no signal'
    — which is the correct answer for most agents most of the time, and saying
    so is the point of having a control at all."""
    if t is None:
        return "no signal"
    if t >= _BONFERRONI_T:
        return "culprit"
    if t <= -_BONFERRONI_T:
        return "protective"
    if t >= 1.8:
        return "leans culprit (not significant)"
    if t <= -1.8:
        return "leans protective (not significant)"
    return "no signal"


def _vote_breakdown(agents: list[dict], was_loss: bool) -> dict:
    """Reconstruct the entry vote exactly as the ensemble computed it.

    This is not an approximation. `session_decisions.agents` persists each
    agent's EFFECTIVE weight — already scaled by that agent's accuracy for the
    action it voted (`ensemble.py`: `s.weight = round(effective_w, 3)`) — and
    the tally is `vote[action] += confidence * effective_weight`. Both numbers
    are stored, so the contest can be replayed from the row.

    On the entry side the contest is BUY vs SELL only; HOLD is an abstention
    and carries no directional signal. BUY wins if its mass beats SELL's by the
    dominance margin AND at least two distinct agents voted it — the rule
    exists because a single flooding agent used to decide alone.

    `decisive` answers the question the panel is really for: if this one agent
    had stayed out, would the trade have happened at all?
    """
    try:
        from app.agents.ensemble import _DIR_DOMINANCE, _DIR_MIN_VOTERS
    except Exception:                                   # keep the panel alive
        _DIR_DOMINANCE, _DIR_MIN_VOTERS = 1.3, 2

    def mass(rows: list[dict], act: str) -> float:
        return sum((r.get("confidence") or 0) * (r.get("weight") or 0)
                   for r in rows if (r.get("action") or "").upper() == act)

    def n_voters(rows: list[dict], act: str) -> int:
        return sum(1 for r in rows if (r.get("action") or "").upper() == act)

    def wins_buy(rows: list[dict]) -> bool:
        bm, sm = mass(rows, "BUY"), mass(rows, "SELL")
        return bm > 0 and bm >= _DIR_DOMINANCE * sm and n_voters(rows, "BUY") >= _DIR_MIN_VOTERS

    buy_mass, sell_mass, hold_mass = mass(agents, "BUY"), mass(agents, "SELL"), mass(agents, "HOLD")
    buy_n, sell_n = n_voters(agents, "BUY"), n_voters(agents, "SELL")

    per: list[dict] = []
    for a in agents:
        act = (a.get("action") or "").upper()
        contrib = (a.get("confidence") or 0) * (a.get("weight") or 0)
        side = {"BUY": buy_mass, "SELL": sell_mass, "HOLD": hold_mass}.get(act, 0.0)
        # Would the entry still have fired without this agent?
        without = [x for x in agents if x is not a]
        decisive = wins_buy(agents) and not wins_buy(without)
        per.append({
            **a,
            "contribution": round(contrib, 3),
            "share_of_side": round(contrib / side, 3) if side else None,
            "decisive": decisive,
            # HOLD is an abstention here, not a correct call — scoring it as one
            # is how SELL became unlearnable on a long-only system.
            "stance": ("argued for the trade" if act == "BUY"
                       else "argued against it" if act == "SELL"
                       else "abstained"),
            "was_right": (not was_loss) if act == "BUY" else (was_loss if act == "SELL" else None),
        })

    ratio = (buy_mass / sell_mass) if sell_mass else None
    return {
        "buy_mass": round(buy_mass, 3), "sell_mass": round(sell_mass, 3),
        "hold_mass": round(hold_mass, 3),
        "buy_voters": buy_n, "sell_voters": sell_n,
        "abstained": n_voters(agents, "HOLD"),
        "dominance_ratio": round(ratio, 2) if ratio else None,
        "dominance_needed": _DIR_DOMINANCE,
        "min_voters": _DIR_MIN_VOTERS,
        "buy_won": wins_buy(agents),
        "agents": sorted(per, key=lambda r: -(r["contribution"] or 0)),
        # The first version asserted "clearing the margin" unconditionally, and
        # said it on a 1.1x contest that had not cleared 1.3x at all — the panel
        # would have been stating the opposite of what happened.
        "summary": (
            f"{buy_n} agents argued to buy with {round(buy_mass, 2)} of weighted conviction, "
            f"against {round(sell_mass, 2)} from {sell_n} arguing not to"
            + ("" if not ratio else
               f" — {round(ratio, 1)}x, clearing the {_DIR_DOMINANCE}x the ensemble needs."
               if wins_buy(agents) else
               f" — only {round(ratio, 1)}x, short of the {_DIR_DOMINANCE}x the ensemble "
               "needs, so the ensemble itself abstained.")
            + ("" if ratio else ". Nothing argued against it.")
        ),
    }


def _decision_path(entry_reason: Optional[str], agents: list[dict]) -> dict:
    """How this trade came to be taken at all.

    The finding that motivated this: on a real losing trade the entry reason
    read "Entry [score 80/78]: 2 agents voted BUY (ensemble HOLD 69%)". The
    ENSEMBLE ABSTAINED. The trade was taken by a separate 0-100 scored gate
    that cleared its threshold by two points. A panel that only showed agent
    votes would leave the reader assuming the panel decided — it did not, and
    that is the single most useful thing to say about how the trade happened.

    Consensus quality is recomputed from the stored votes because the gate's
    own deductions are only persisted when it BLOCKS. When it lets a trade
    through, the reasons it nearly did not are discarded, so they are rebuilt
    here from the same constants the gate uses.
    """
    import re

    out: dict = {"entry_reason": entry_reason}
    if entry_reason:
        m = re.search(r"score\s+(\d+(?:\.\d+)?)\s*/\s*(\d+)", entry_reason)
        if m:
            out["score"] = float(m.group(1))
            out["score_min"] = float(m.group(2))
            out["margin"] = round(out["score"] - out["score_min"], 1)
        e = re.search(r"ensemble\s+(BUY|SELL|HOLD)\s+(\d+)%", entry_reason)
        if e:
            out["ensemble_action"] = e.group(1)
            out["ensemble_confidence_pct"] = int(e.group(2))
            out["ensemble_abstained"] = e.group(1) == "HOLD"

    try:
        from app.services.sessions_service import (
            _RELIABLE_BUY_AGENTS, _DISCOUNTED_SELLERS)
    except Exception:
        _RELIABLE_BUY_AGENTS = frozenset({"gbm", "meanrev", "memory", "day_structure"})
        _DISCOUNTED_SELLERS = frozenset({"day_structure", "meanrev"})

    def act(a):
        return (a.get("action") or "").upper()

    buys = [a["agent"] for a in agents if act(a) == "BUY"]
    # Structural sellers are discounted by the gate itself: day_structure and
    # meanrev sell BY CONSTRUCTION where the entry band opens, so their SELL is
    # a statement about position in range, not directional dissent.
    sells = [a["agent"] for a in agents
             if act(a) == "SELL" and a["agent"] not in _DISCOUNTED_SELLERS]
    reliable = [b for b in buys if b in _RELIABLE_BUY_AGENTS]
    net = len(buys) - len(sells)

    discounted = [a["agent"] for a in agents
                  if act(a) == "SELL" and a["agent"] in _DISCOUNTED_SELLERS]

    out["buy_voters"] = sorted(buys)
    out["counted_sell_voters"] = sorted(sells)
    out["discounted_sell_voters"] = sorted(discounted)
    out["net_consensus"] = net
    out["reliable_cosigners"] = sorted(reliable)
    notes: list[str] = []
    if discounted:
        # Without this the panel contradicts itself: the vote table shows N
        # agents arguing against, while the gate's net consensus counted none
        # of them. Both are true, and the reason is the point.
        notes.append(
            f"{', '.join(discounted)} voted SELL but {'was' if len(discounted) == 1 else 'were'} "
            "not counted as dissent — the gate discounts these agents by design, either "
            "because they sell structurally wherever the entry band opens, or because "
            "their SELL was measured as anti-predictive.")
    if net <= 0:
        notes.append(
            f"The panel was divided — {len(buys)} for, {len(sells)} against, net {net}. "
            "Divided panels historically lose, and the gate docks points for it rather "
            "than refusing outright.")
    elif net == 1:
        notes.append(
            f"Thin consensus: {len(buys)} for against {len(sells)}, net 1. The gate treats "
            "this as a 25%-win class and deducts accordingly.")
    if not reliable:
        notes.append(
            "No agent with a demonstrated BUY edge (gbm, meanrev, memory or day_structure) "
            "backed this entry — the consensus was made of agents with no proven edge on "
            "the buy side.")
    else:
        notes.append(f"Backed by a proven BUY voter: {', '.join(reliable)}.")
    if out.get("ensemble_abstained"):
        notes.insert(0,
            f"The ensemble itself did NOT want this trade — it voted HOLD at "
            f"{out.get('ensemble_confidence_pct')}%. The entry came from the scored gate, "
            f"which reached {out.get('score')} against a {out.get('score_min')} threshold.")
    if out.get("margin") is not None and 0 <= out["margin"] <= 5:
        notes.append(
            f"It cleared the threshold by {out['margin']} points. Anything that shaved a "
            "few more would have blocked it.")
    out["notes"] = notes
    return out


async def trade_postmortem(trade_id: str) -> dict:
    """Everything known about why ONE trade ended the way it did.

    Assembled from three sources because no single one has the whole picture:
      • trade_records  — prices, P&L, exit_reason, the gate-time agent map
      • session_decisions — the rich per-agent vote at the entry bar (weight,
        confidence and the agent's own stated reasoning), plus entry indicators
      • the 1-second candle store — the price PATH between entry and exit

    The path is what turns "it lost" into a cause. A trade that went +0.8% and
    round-tripped is a different failure from one that never traded green, and
    the trade record alone cannot tell them apart — it only holds the endpoints.
    """
    from sqlalchemy import text
    from app.database.postgres import engine

    async with engine.begin() as conn:
        tr = (await conn.execute(text("""
            SELECT trade_id, symbol, action, entry_price, exit_price, pnl_pct,
                   pnl_abs, duration_minutes, timestamp_open, timestamp_close,
                   market_context, agent_signals, session_id, ensemble_confidence,
                   trade_source, outcome
            FROM trade_records WHERE trade_id = :t
        """), {"t": trade_id})).fetchone()

    if not tr:
        return {"error": f"no trade {trade_id}"}

    def _d(v):
        return v if isinstance(v, dict) else (json.loads(v) if v else {})

    mc, sig = _d(tr[10]), _d(tr[11])
    session_id, symbol = tr[12], (tr[1] or "").upper()
    entry_price, exit_price = tr[3], tr[4]
    pnl_pct = float(tr[5]) * 100.0 if tr[5] is not None else None
    opened, closed = tr[8], tr[9]

    # ── Rich agent votes at the entry bar ────────────────────────────────────
    # trade_records.agent_signals is a flat {agent: action} map written at the
    # gate. session_decisions holds the same vote WITH its weight, confidence
    # and the agent's own reasoning — which is the part that actually explains
    # anything. Match on the entry minute.
    agents: list[dict] = []
    indicators: dict = {}
    entry_reason = None
    entry_hhmm = opened.astimezone(IST).strftime("%H:%M") if opened else None
    if session_id and entry_hhmm:
        async with engine.begin() as conn:
            row = (await conn.execute(text("""
                SELECT agents, indicators, reason, price
                FROM session_decisions
                WHERE session_id = :s AND candle_time = :c
                LIMIT 1
            """), {"s": session_id, "c": entry_hhmm})).fetchone()
        if row:
            raw = row[0] if isinstance(row[0], list) else (json.loads(row[0]) if row[0] else [])
            agents = [{"agent": a.get("agent"), "action": a.get("action"),
                       "weight": a.get("weight"), "confidence": a.get("confidence"),
                       "reasoning": a.get("reasoning")} for a in raw]
            indicators = _d(row[1])
            entry_reason = row[2]

    if not agents and sig:
        # Session detail pruned — fall back to the flat gate-time map, with the
        # reasoning fields absent rather than invented.
        agents = [{"agent": k, "action": v, "weight": None,
                   "confidence": None, "reasoning": None}
                  for k, v in sorted(sig.items())]

    setup = classify_setup(indicators, entry_price) if indicators else None

    # ── Price path between entry and exit ────────────────────────────────────
    path: dict = {}
    try:
        from app.data.candle_store import read_bars
        day = opened.astimezone(IST).date().isoformat() if opened else None
        if day and entry_price:
            bars = await asyncio.to_thread(read_bars, symbol, day, 60)
            o_hhmm = entry_hhmm
            c_hhmm = closed.astimezone(IST).strftime("%H:%M") if closed else None
            window = [b for b in bars
                      if (not o_hhmm or str(b.get("time", ""))[:5] >= o_hhmm)
                      and (not c_hhmm or str(b.get("time", ""))[:5] <= c_hhmm)]
            highs = [b["high"] for b in window if b.get("high") is not None]
            lows = [b["low"] for b in window if b.get("low") is not None]
            if highs and lows:
                mfe = (max(highs) - entry_price) / entry_price * 100.0
                mae = (min(lows) - entry_price) / entry_price * 100.0
                # What the price did AFTER we were out. This is the only way to
                # separate "the entry was wrong" from "the stop was too tight":
                # both look like a loss, but only one of them was recoverable.
                after = [b for b in bars if c_hhmm and str(b.get("time", ""))[:5] > c_hhmm]
                after_highs = [b["high"] for b in after if b.get("high") is not None]
                rebound = ((max(after_highs) - entry_price) / entry_price * 100.0
                           if after_highs else None)
                path = {
                    "bars": len(window),
                    "best_pct": round(mfe, 3),      # max favourable excursion
                    "worst_pct": round(mae, 3),     # max adverse excursion
                    "ever_green": mfe > 0,
                    # Did it hand back a gain it actually had?
                    "gave_back_pct": (round(mfe - (pnl_pct or 0.0), 3)
                                      if pnl_pct is not None and mfe > 0 else None),
                    "bars_after_exit": len(after),
                    "best_after_exit_pct": round(rebound, 3) if rebound is not None else None,
                    # Hindsight, and labelled as such in the UI: would simply
                    # holding have cleared the round trip?
                    "recovered_after_exit": (rebound is not None
                                             and rebound > _ROUND_TRIP_COST_PCT),
                    # Carried so the UI states the cost hurdle instead of
                    # hardcoding it, and — more importantly — so it can say
                    # whether THIS trade cleared it rather than printing a
                    # general remark that reads as a verdict.
                    "round_trip_cost_pct": _ROUND_TRIP_COST_PCT,
                    "peak_was_bankable": mfe > _ROUND_TRIP_COST_PCT,
                }
    except Exception as exc:
        logger.debug("price path unavailable for %s: %s", trade_id, exc)

    exit_reason = mc.get("exit_reason")
    held = tr[7]
    is_loss = pnl_pct is not None and pnl_pct < 0

    # ── Plain-language cause, built from the path, not guessed ───────────────
    causes: list[str] = []
    if is_loss:
        causes.append(_loss_reason(exit_reason, pnl_pct, held))
        if path:
            best = path["best_pct"]
            if not path["ever_green"]:
                causes.append(
                    f"It never traded above the entry — best was {best}%. "
                    "The entry was wrong from the first bar, so no exit rule could have saved it.")
            elif best < _ROUND_TRIP_COST_PCT:
                # The gate is the cost, not an arbitrary number: a gain smaller
                # than the round trip was never bankable, so calling it "a move
                # the exit failed to keep" blames the wrong half of the system.
                causes.append(
                    f"It only ever reached {best}%, below the {_ROUND_TRIP_COST_PCT}% "
                    "round-trip cost, so there was never a gain that could have been "
                    "banked. This is an entry that did not work, not an exit that let "
                    f"one go; it then ran to {path['worst_pct']}%.")
            elif path.get("gave_back_pct") and path["gave_back_pct"] > 0.3:
                causes.append(
                    f"It was up {best}% at best and gave back "
                    f"{path['gave_back_pct']} points before exiting — the entry found a "
                    "real move, the exit did not keep it.")
            else:
                causes.append(
                    f"It reached {best}% at best and {path['worst_pct']}% at worst.")
        if setup and SETUP_EDGE_PP.get(setup, 0) < 0:
            est = "measured" if setup in SETUP_ESTABLISHED else "directional, not established"
            causes.append(
                f"The entry sits in '{setup}', a setup that returns "
                f"{SETUP_EDGE_PP[setup]}pp versus the same day's other entries ({est}).")

    # ── Who is to blame ─────────────────────────────────────────────────────
    # One target, in plain words, because "here are the facts, you decide" is
    # what the panel already did and it left the reader to do the reasoning.
    # The split that matters is entry vs exit, and the price path decides it:
    #   never had a bankable gain      -> the ENTRY. No exit rule saves this.
    #   had a real gain, ended down    -> the EXIT. The entry found the move.
    #   lost, but recovered after out  -> the STOP. It was right, just too tight.
    voted_buy_names = sorted(a["agent"] for a in agents
                             if (a.get("action") or "").upper() == "BUY")
    blame: dict = {}
    if is_loss:
        best = path.get("best_pct")
        if not path:
            blame = {"target": "unknown", "headline": "Not enough price history to attribute this loss",
                     "detail": "The 1-minute bars for this day are not in the store, so how far the "
                               "trade went in each direction cannot be reconstructed."}
        elif path.get("recovered_after_exit"):
            # Checked BEFORE the entry test, and for every exit rule rather than
            # just stops. If the name went on to clear the round trip while we
            # were out, the direction was right and the exit was early — calling
            # that a bad entry blames the wrong half, whichever rule fired.
            stopped = "stop" in (exit_reason or "").lower()
            blame = {
                "target": "exit",
                "headline": "The stop was too tight" if stopped else "It was closed too early",
                # `pnl_pct` is a raw float here — unformatted it renders as
                # "-1.6099999999999999%", which reads as a precision the number
                # does not have and undermines a panel whose whole job is to be
                # believed.
                "detail": (
                    f"We came out at {pnl_pct:.2f}%, and the price then reached "
                    f"{path['best_after_exit_pct']}% above the entry over the following "
                    f"{path['bars_after_exit']} minutes. The direction was right; the trade "
                    "was not given room." if stopped else
                    f"We came out at {pnl_pct:.2f}% on the {_loss_reason(exit_reason, pnl_pct, held)}, "
                    f"and the price then reached {path['best_after_exit_pct']}% above the entry "
                    f"over the following {path['bars_after_exit']} minutes. The entry was "
                    "vindicated; the exit fired first."),
                "hindsight": True,
            }
        elif best is not None and best < _ROUND_TRIP_COST_PCT:
            blame = {
                "target": "entry",
                "headline": "The entry was wrong",
                "detail": (f"It never got further than {best}% above the entry — less than the "
                           f"{_ROUND_TRIP_COST_PCT}% it costs to round-trip a position. There was "
                           "never a profit here to protect, so no exit rule could have rescued it."),
                "agents_implicated": voted_buy_names,
            }
        elif path.get("gave_back_pct") and path["gave_back_pct"] > 0.3:
            blame = {
                "target": "exit",
                "headline": "The exit gave back a real gain",
                "detail": (f"It was up {best}% at best and still closed at {pnl_pct:.2f}% — "
                           f"{path['gave_back_pct']} points handed back. The entry found the move; "
                           "the exit did not keep it."),
            }
        else:
            blame = {
                "target": "unclear",
                "headline": "No single cause stands out",
                "detail": (f"It reached {best}% at best and {path.get('worst_pct')}% at worst, "
                           "then closed near the middle. Small enough that this is the cost floor "
                           "rather than a mistake."),
            }
        if setup and SETUP_EDGE_PP.get(setup, 0) < 0 and setup in SETUP_ESTABLISHED:
            blame["contributing"] = (
                f"The setup was '{setup}', which is measured at {SETUP_EDGE_PP[setup]}pp against "
                "the same day's other entries — a cell known to do worse than average.")

    baseline = await baseline_if_warm()
    by_agent = {a["agent"]: a for a in baseline.get("agents", [])}
    for a in agents:
        b = by_agent.get(a["agent"]) or {}
        a["baseline_verdict"] = b.get("verdict")
        a["baseline_lift"] = b.get("lift")
        a["baseline_t"] = b.get("t_stat")

    voted_buy = [a["agent"] for a in agents if (a.get("action") or "").upper() == "BUY"]
    return {
        "trade_id": tr[0], "symbol": symbol, "action": tr[2],
        "source": tr[14], "outcome": tr[15], "session_id": session_id,
        "entry_price": entry_price, "exit_price": exit_price,
        "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
        "pnl_abs": tr[6], "held_minutes": held,
        "opened_at": str(opened) if opened else None,
        "closed_at": str(closed) if closed else None,
        "is_loss": is_loss,
        "ensemble_confidence": tr[13],
        "entry_reason": entry_reason,
        "setup": setup,
        "setup_edge_pp": SETUP_EDGE_PP.get(setup) if setup else None,
        "setup_established": setup in SETUP_ESTABLISHED if setup else False,
        "indicators": indicators,
        "price_path": path,
        "exit_reason": exit_reason,
        "loss_reason": _loss_reason(exit_reason, pnl_pct, held) if is_loss else None,
        "blame": blame,
        "causes": causes,
        "agents": agents,
        "vote": _vote_breakdown(agents, is_loss) if agents else None,
        "decision_path": _decision_path(entry_reason, agents) if agents else None,
        "voted_buy": sorted(voted_buy),
        "n_agents": len(agents),
        "culprit_note": (
            "Agent verdicts are corpus-wide and day-clustered; they say nothing "
            "about this trade. A single trade cannot implicate an agent, and a "
            "ranking taken from executed trades is biased by the gate that "
            "allowed them."
        ),
    }


_NARRATIVE_KEY = "ai_engine:session_narrative:{}"
_NARRATIVE_TTL = 7 * 24 * 3600

_NARRATIVE_SYSTEM = (
    "You are a trading analyst writing a short post-mortem for one session. "
    "You are given facts that have already been computed and verified. "
    "Explain them. Do NOT compute, estimate or invent any number, and do NOT "
    "decide which agent is at fault — the culpability verdicts are given to "
    "you and are the only ones you may state.\n"
    "Use the supplied glossary for every technical term; do not infer what a "
    "term means from its name.\n"
    "The agent verdicts are corpus-wide statistics. NEVER claim an agent "
    "caused, worsened or softened anything that happened in THIS session — "
    "the verdicts say nothing about individual trades.\n"
    "If a field is null or a note says something was not retained, you must "
    "say it is unknown. Do not reason about it, and do not conclude anything "
    "about it either way.\n"
    "If the facts do not support a conclusion, say so plainly. Prefer 'the "
    "evidence does not say' over a plausible guess. Write 4-8 sentences of "
    "plain prose, no headings, no bullet points, no markdown."
)

# The model does not get to infer what these words mean. Left to itself it read
# "protective" as "made this session's losses less severe" — a causal claim
# about individual trades from a corpus-wide rate difference, which is exactly
# the reasoning the measurement is built to prevent.
_GLOSSARY = {
    "setup": "A rule-based label for market conditions at entry. Never uses the outcome.",
    "setup_edge_vs_other_entries_pp": (
        "How this setup performed against the SAME DAY's other entries, in "
        "percentage points, averaged across days. Positive means it lost LESS "
        "than average. It never means profitable."),
    "setup_edge_is_statistically_established": (
        "True only if the day-clustered t-statistic clears correction for "
        "multiple testing. False means directional at best — do not treat it "
        "as a finding."),
    "verdict 'culprit'": (
        "Across the whole corpus this agent votes BUY more often before losing "
        "decisions than before winning ones. It is a statistical tendency over "
        "many days, NOT a statement that it caused any particular trade."),
    "verdict 'protective'": (
        "Across the whole corpus this agent votes BUY more often before WINNING "
        "decisions than before losing ones, i.e. its BUY vote discriminates. It "
        "does NOT mean the agent reduced, softened or limited any loss, and it "
        "says nothing about this session's trades."),
    "verdict 'leans ...'": (
        "Directional only, below the significance bar. Report it as not "
        "established."),
    "is_statistically_established": (
        "Whether this verdict clears correction for multiple testing. Use this "
        "field verbatim — do NOT judge significance yourself from the t value. "
        "A verdict of 'culprit' or 'protective' is established; anything "
        "'leans ...' is not."),
    "how_it_exited": "The mechanical exit rule that closed the trade. A fact, not a diagnosis.",
}


def _narrative_facts(report: dict) -> dict:
    """The strictly-factual subset handed to the model.

    Everything here was computed by rules or by a day-clustered measurement.
    The model gets no raw prices, no P&L it could total up differently, and no
    invitation to rank the agents — only the verdicts already established.
    """
    trades = []
    for t in report.get("trades", []):
        trades.append({
            "outcome": "loss" if t.get("is_loss") else "win",
            "pnl_pct": t.get("pnl_pct"),
            "setup": t.get("setup"),
            "setup_note": t.get("setup_note"),
            "setup_edge_vs_other_entries_pp": t.get("setup_edge_pp"),
            "setup_edge_is_statistically_established": t.get("setup_established"),
            "how_it_exited": t.get("loss_reason"),
            "agents_that_voted_buy": t.get("voted_buy"),
        })
    return {
        "glossary": _GLOSSARY,
        "symbol": report.get("symbol"),
        "date": report.get("date"),
        "n_trades": report.get("n_trades"),
        "n_losses": report.get("n_losses"),
        "trades": trades,
        # `is_statistically_established` is stated, not implied by the t. Asked
        # to judge significance itself the model got it backwards, calling
        # meanrev (t=-3.15, clears correction) "not significant" while the
        # taxonomy already encodes the answer in the verdict string.
        "agent_culpability_verdicts": [
            {"agent": a["agent"], "verdict": a["verdict"],
             "lift": a["baseline_lift"], "t": a["baseline_t"],
             "is_statistically_established":
                 a["verdict"] in ("culprit", "protective")}
            for a in report.get("agent_attribution", [])
            if a.get("verdict") not in (None, "no signal")
        ],
        "overall_culprit_finding": report.get("culprit_verdict"),
        "context_every_reader_needs": (
            "Every setup edge below is RELATIVE to the same day's other entries. "
            "All absolute cells are negative and already net of the 0.125% "
            "round-trip cost, so a positive edge means 'lost less', never "
            "'profitable'. A single session cannot establish which agent is at "
            "fault; the verdicts given are measured across the whole corpus."
        ),
    }


async def session_narrative(report: dict, force: bool = False) -> Optional[str]:
    """LLM prose over the computed facts. Never on the trading path.

    The model is deliberately downstream of every judgement. Measured
    2026-09-03/04, this 8B put 74% of trades into a single setup tag where the
    deterministic rule spread the same corpus across nine, and its free-text
    loss labels fragmented into paraphrases ("chased" vs "chasing" momentum
    counted as two failure modes). It is poor at deciding and fine at
    explaining, so it is given the decisions and asked only to narrate them.

    Returns None when the LLM is off or unreachable — the post-mortem is fully
    usable without it.
    """
    from app.utils.redis_client import cache_get, cache_set
    from app.utils.llm_client import llm_chat

    sid = report.get("session_id") or ""
    key = _NARRATIVE_KEY.format(sid)
    if not force:
        try:
            cached = await cache_get(key)
            if cached:
                return cached
        except Exception:
            logger.debug("narrative cache read failed", exc_info=True)

    if not report.get("trades"):
        return None

    facts = _narrative_facts(report)
    # Only ask about setups when at least one trade has one. Left in
    # unconditionally, the "where setup is not null" clause invited a vacuous
    # sentence about a non-existent trade on sessions where none were retained.
    has_setup = any(t.get("setup") for t in facts["trades"])
    setup_ask = (
        " Say whether each trade's setup sits in a cell measured as worse than "
        "average."
        if has_setup else
        " No setup was retained for this session, so state that the entry "
        "conditions are unknown and draw no conclusion about them."
    )
    prompt = (
        "Write the post-mortem for this trading session using ONLY these facts.\n\n"
        + json.dumps(facts, indent=2, default=str)
        + "\n\nCover: what the session did and how each losing trade actually "
          "ended." + setup_ask +
        " Then state what the agent evidence does and does not establish, "
        "remembering it is corpus-wide and says nothing about these particular "
        "trades. State plainly if no agent is implicated."
    )
    try:
        txt = await llm_chat(prompt, system=_NARRATIVE_SYSTEM,
                             temperature=0.2, max_tokens=520, timeout=60.0)
    except Exception:
        logger.debug("session narrative call failed for %s", sid, exc_info=True)
        return None
    if not txt or not txt.strip():
        return None
    txt = txt.strip()
    try:
        await cache_set(key, txt, expire=_NARRATIVE_TTL)
    except Exception:
        logger.debug("narrative cache write failed", exc_info=True)
    return txt


async def session_postmortem(session_id: str, narrative: bool = False,
                             force_narrative: bool = False) -> dict:
    """Full post-mortem for one session."""
    from sqlalchemy import text
    from app.database.postgres import engine

    async with engine.begin() as conn:
        meta = (await conn.execute(text("""
            SELECT symbol, mode, date, status, trade_count, win_count,
                   total_pnl_abs, total_pnl_pct
            FROM session_metadata WHERE session_id = :s
        """), {"s": session_id})).fetchone()

        rows = (await conn.execute(text("""
            SELECT candle_time, price, action, reason, indicators, agents, trade,
                   cf_pnl_pct
            FROM session_decisions
            WHERE session_id = :s AND executed = TRUE
              AND trade IS NOT NULL AND trade::text <> '{}'
            ORDER BY candle_time
        """), {"s": session_id})).fetchall()

        # session_metadata only starts 2026-06-29 and paper sessions do not all
        # write a row, so symbol/date must fall back to the decisions themselves
        # rather than rendering a post-mortem with a blank header.
        fallback = (await conn.execute(text("""
            SELECT symbol, (min(created_at) AT TIME ZONE 'Asia/Kolkata')::date::text
            FROM session_decisions WHERE session_id = :s GROUP BY symbol LIMIT 1
        """), {"s": session_id})).fetchone()

        # exit_reason lives on the closed trade, not the decision row. This is
        # also the fallback source when session_decisions have been pruned.
        treads = (await conn.execute(text("""
            SELECT timestamp_open, pnl_pct, duration_minutes, market_context,
                   entry_price, exit_price, timestamp_close, agent_signals, symbol
            FROM trade_records WHERE session_id = :s ORDER BY timestamp_open
        """), {"s": session_id})).fetchall()

    def _asdict(v):
        return v if isinstance(v, dict) else (json.loads(v) if v else {})

    exit_reasons = []
    for tr in treads:
        mc = _asdict(tr[3])
        exit_reasons.append({
            "pnl_pct": tr[1], "held": tr[2],
            "exit_reason": (mc or {}).get("exit_reason"),
        })

    # Pair BUY -> next SELL. The agents that matter are the ones on the ENTRY
    # bar: they are what argued for taking the trade at all.
    trades: list[dict] = []
    pending: Optional[dict] = None
    for candle_time, price, action, reason, ind, agents, trade, cf in rows:
        ind = ind if isinstance(ind, dict) else (json.loads(ind) if ind else {})
        agents = agents if isinstance(agents, list) else (json.loads(agents) if agents else [])
        trade = trade if isinstance(trade, dict) else (json.loads(trade) if trade else {})
        act = (trade.get("action") or action or "").upper()
        if act == "BUY":
            pending = {
                "entry_time": candle_time, "entry_price": price,
                "setup": classify_setup(ind, price),
                "indicators": ind, "entry_reason": reason,
                "agents": [{"agent": a.get("agent"), "action": a.get("action"),
                            "weight": a.get("weight"), "confidence": a.get("confidence"),
                            "reasoning": a.get("reasoning")} for a in agents],
            }
        elif act == "SELL" and pending is not None:
            pnl_abs = trade.get("pnl")
            entry = pending["entry_price"] or 0
            pnl_pct = ((price - entry) / entry * 100.0) if entry else None
            er = exit_reasons[len(trades)] if len(trades) < len(exit_reasons) else {}
            pending.update({
                "exit_time": candle_time, "exit_price": price,
                "pnl_abs": pnl_abs, "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
                "is_loss": (pnl_abs is not None and pnl_abs < 0),
                "exit_reason": er.get("exit_reason"),
                "loss_reason": _loss_reason(er.get("exit_reason"), pnl_pct, er.get("held")),
                "setup_edge_pp": SETUP_EDGE_PP.get(pending["setup"]),
                "setup_established": pending["setup"] in SETUP_ESTABLISHED,
                "voted_buy": sorted(a["agent"] for a in pending["agents"]
                                    if (a.get("action") or "").upper() == "BUY"),
                "voted_against": sorted(a["agent"] for a in pending["agents"]
                                        if (a.get("action") or "").upper() in ("SELL", "HOLD")),
            })
            pending["source"] = "session_decisions"
            trades.append(pending)
            pending = None

    if not trades and treads:
        # `session_decisions` is pruned on a retention schedule, so older
        # sessions keep their closed trades but lose the per-bar record. Rather
        # than report "0 trades" for a session that demonstrably traded, rebuild
        # from trade_records.
        #
        # The setup tag is deliberately NOT emitted here: market_context carries
        # only rsi/vwap/regime, and classify_setup needs sma5/sma20/atr too. It
        # would silently return `no_clear_setup` for everything — a tag that now
        # carries a measured POSITIVE edge, so the panel would be quietly
        # reassuring about trades it cannot actually classify.
        for tr in treads:
            mc, sig = _asdict(tr[3]), _asdict(tr[7])
            entry, exit_p, pnl = tr[4], tr[5], tr[1]
            pnl_pct = float(pnl) * 100.0 if pnl is not None else None
            agents = [{"agent": k, "action": v, "weight": None,
                       "confidence": None, "reasoning": None}
                      for k, v in sorted(sig.items())]
            trades.append({
                "source": "trade_records",
                "entry_time": str(tr[0]), "entry_price": entry,
                "exit_time": str(tr[6]), "exit_price": exit_p,
                "pnl_abs": None,
                "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
                "is_loss": (pnl_pct is not None and pnl_pct < 0),
                "setup": None,
                "setup_note": "entry indicators not retained for this session",
                "setup_edge_pp": None, "setup_established": False,
                "indicators": {k: v for k, v in (mc or {}).items()
                               if k in ("rsi", "vwap", "regime")},
                "entry_reason": None,
                "exit_reason": (mc or {}).get("exit_reason"),
                "loss_reason": _loss_reason((mc or {}).get("exit_reason"), pnl_pct, tr[2]),
                "agents": agents,
                "voted_buy": sorted(k for k, v in sig.items() if str(v).upper() == "BUY"),
                "voted_against": sorted(k for k, v in sig.items()
                                        if str(v).upper() in ("SELL", "HOLD")),
            })

    baseline = await baseline_if_warm()
    by_agent = {a["agent"]: a for a in baseline.get("agents", [])}

    # Descriptive session tally: how often each agent voted BUY on this
    # session's losers vs its winners. Explicitly NOT a verdict — with 1-5
    # trades it is noise, and it is presented alongside the corpus baseline so
    # that is impossible to miss.
    losers = [t for t in trades if t.get("is_loss")]
    winners = [t for t in trades if not t.get("is_loss")]
    attribution = []
    for agent, base in sorted(by_agent.items(),
                              key=lambda kv: -(kv[1].get("lift") or 0)):
        bl = sum(1 for t in losers if agent in t["voted_buy"])
        bw = sum(1 for t in winners if agent in t["voted_buy"])
        attribution.append({
            "agent": agent,
            "bought_losers": f"{bl}/{len(losers)}",
            "bought_winners": f"{bw}/{len(winners)}",
            "baseline_lift": base.get("lift"),
            "baseline_t": base.get("t_stat"),
            "baseline_days": base.get("days"),
            "verdict": base.get("verdict"),
        })

    culprits = [a for a in attribution if a["verdict"] == "culprit"]
    report = {
        "session_id": session_id,
        "symbol": (meta[0] if meta else None) or (fallback[0] if fallback else None),
        "mode": meta[1] if meta else None,
        "date": (meta[2] if meta else None) or (fallback[1] if fallback else None),
        "status": meta[3] if meta else None,
        "trades": trades,
        "n_trades": len(trades), "n_losses": len(losers),
        "total_pnl_pct": meta[7] if meta else None,
        "agent_attribution": attribution,
        "culprit_verdict": (
            ", ".join(a["agent"] for a in culprits) if culprits else
            "No agent is a statistically established culprit. Per-session BUY "
            "counts above are descriptive only — a single session cannot "
            "identify one, and per-agent rankings taken from executed trades "
            "are biased by the entry gate that allowed them."
        ),
        "method": baseline.get("method"),
    }

    if narrative:
        # Narrative last, and additive: it reads the finished report and cannot
        # change a single field of it.
        report["narrative"] = await session_narrative(report, force=force_narrative)
        report["narrative_note"] = (
            "LLM prose over the computed facts above. It assigns no tag, ranks "
            "no agent and produces no number — those are all measured. Absent "
            "when the LLM is unavailable."
        )
    return report
