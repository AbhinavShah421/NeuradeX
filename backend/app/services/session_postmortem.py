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

import json
from typing import Any, Optional

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


async def session_postmortem(session_id: str) -> dict:
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

    baseline = await agent_culpability_baseline()
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
    return {
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
