"""LLM rejection reviewer — the LLM pointed at what the gate THREW AWAY.

The existing entry reviewer (llm_entry_review) sits after the gate, so it only
ever sees trades that already passed. It approved 93% of them, and after three
weeks had produced 41 reviews of which 15 carried an outcome label — not enough
to decide anything, and structurally unable to find what the gate missed.

The information is on the other side. The gate rejects ~250,000 decisions for
every ~600 it takes, and every rejection that lands on a symbol in the tick
store already gets a counterfactual P&L label. So a reviewer aimed at
rejections is both far better supplied AND self-scoring: we know exactly what
each skipped trade would have returned.

Concretely, on 2026-07-31 HEXT was blocked on 197 consecutive bars for "panel
dissent: 2 BUY - 2 SELL = 0" while the stock rose in 86.6% of 60-minute
windows. Nothing in the current setup could surface that; the LLM never saw a
single one of those bars because none became a trade.

Design notes:
  • Runs OFF-HOURS in a batch, never on the trading hot path. The 8B model
    needs seconds per call; latency is irrelevant here.
  • Samples NEAR-MISSES — decisions with real buy-side interest — rather than
    the whole rejection pile, which is dominated by obvious no-trades.
  • The dossier carries ONLY what was knowable at the decision bar. The
    counterfactual outcome is deliberately withheld from the prompt: the whole
    point is to score the LLM against an answer it could not see.
  • Verdicts are logged, never acted on, exactly like the entry reviewer. It
    earns influence by beating the base rate on labelled rejections first.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Sampling / budget
_MIN_BUY_VOTES   = 2      # "near miss" — the panel had real buy-side interest
_DAILY_CAP       = 40     # LLM calls per sweep (8B on CPU is seconds per call)
_SWEEP_HOUR_IST  = 4      # 04:00 IST, after the 03:00 loss-learning run

_DDL = """
CREATE TABLE IF NOT EXISTS llm_rejection_reviews (
    id            SERIAL PRIMARY KEY,
    decision_id   BIGINT NOT NULL,
    symbol        TEXT NOT NULL,
    trade_date    DATE NOT NULL,
    candle_time   TEXT NOT NULL,
    price         DOUBLE PRECISION,
    verdict       TEXT,           -- should_enter | agree_skip | parse_error | no_llm
    confidence    DOUBLE PRECISION,
    reason        TEXT,
    model         TEXT,
    latency_ms    INTEGER,
    dossier       JSONB,
    cf_pnl_pct    DOUBLE PRECISION,   -- copied at review time; the answer key
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (decision_id)
);
"""
_ddl_ready = False

def _system_prompt(base_win_pct: float, base_avg_pnl: float) -> str:
    """Calibrated reviewer prompt.

    The obvious phrasing ("catch the ones it should have taken") produces a
    model that says yes to everything: on the first 60 reviews llama3.1:8b
    returned should_enter 60/60 with only 3 distinct confidence values — a
    template, not a judgement. The entry reviewer showed the same pathology
    from the other side, approving 93% of what it saw. An endorser that never
    declines carries no information whichever way you point it.

    Stating the population's actual base rate and naming an explicit quota
    fixes it: the same 20 rejections went to should_enter 1/20 with 5 distinct
    confidences. The numbers are measured from the live data rather than
    hardcoded, so the calibration keeps tracking the system as the gate moves.
    """
    return (
        "You are auditing intraday long-only trade setups that a rules-based "
        "gate DECLINED. Ground truth on this exact population: only "
        f"{base_win_pct:.0f}% would have been profitable and the average "
        f"outcome is {base_avg_pnl:+.3f}% AFTER costs. The gate is usually "
        "right. Reserve 'should_enter' for the clear minority with an "
        "unusually strong case — if you flag more than about 1 in 5 you are "
        "miscalibrated. Vary your confidence honestly; do not reuse one value. "
        'Reply with ONLY a JSON object: {"verdict": "should_enter" or '
        '"agree_skip", "confidence": 0.0-1.0, "reason": "<one sentence>"}'
    )


async def _base_rates(day: str) -> tuple[float, float]:
    """Actual win% and avg P&L of the near-miss rejection population, so the
    prompt calibrates against reality instead of a stale constant."""
    from sqlalchemy import text
    from app.database.postgres import engine
    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text("""
                SELECT (avg((d.cf_pnl_pct > 0)::int::float) * 100.0)::float,
                       avg(d.cf_pnl_pct)::float
                FROM session_decisions d
                LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
                WHERE d.executed = FALSE AND d.cf_pnl_pct IS NOT NULL
                  AND d.candle_time < '13:00'
                  AND (SELECT count(*) FROM jsonb_array_elements(d.agents) a
                       WHERE a->>'action' = 'BUY') >= :minbuy
            """), {"minbuy": _MIN_BUY_VOTES})).fetchone()
        if row and row[0] is not None:
            return float(row[0]), float(row[1] or 0.0)
    except Exception as exc:
        logger.debug("base-rate lookup failed, using fallback: %s", exc)
    return 36.0, -0.085


async def _ensure_table() -> None:
    global _ddl_ready
    if _ddl_ready:
        return
    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        await conn.execute(text(_DDL))
    _ddl_ready = True


def _parse_verdict(reply: str) -> tuple[str, float | None, str]:
    try:
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        d = json.loads(m.group(0)) if m else {}
        v = str(d.get("verdict", "")).lower()
        if v not in ("should_enter", "agree_skip"):
            return "parse_error", None, reply[:300]
        conf = d.get("confidence")
        return v, (float(conf) if conf is not None else None), str(d.get("reason", ""))[:300]
    except Exception:
        return "parse_error", None, (reply or "")[:300]


def _build_dossier(row) -> dict:
    """Decision-time snapshot ONLY. The counterfactual result is never included —
    the reviewer has to earn its score without seeing the answer."""
    _id, symbol, day, ctime, price, reason, agents_raw, ind_raw, _cf = row
    try:
        agents = agents_raw if isinstance(agents_raw, list) else json.loads(agents_raw or "[]")
    except Exception:
        agents = []
    try:
        ind = ind_raw if isinstance(ind_raw, dict) else json.loads(ind_raw or "{}")
    except Exception:
        ind = {}
    votes = [{"agent": a.get("agent"), "vote": a.get("action"),
              "conf": round(float(a.get("confidence") or 0), 2)}
             for a in agents if isinstance(a, dict)]
    return {
        "symbol": symbol,
        "time": ctime,
        "price": price,
        "buy_votes": sum(1 for v in votes if v["vote"] == "BUY"),
        "sell_votes": sum(1 for v in votes if v["vote"] == "SELL"),
        "votes": votes,
        "indicators": {k: ind.get(k) for k in
                       ("rsi", "vwap", "sma5", "sma20", "mom5", "atr")},
        "gate_rejection_reason": (reason or "")[:400],
    }


async def _review_one(row, system: str) -> str:
    from app.utils.llm_client import llm_chat, active_model
    dossier = _build_dossier(row)
    decision_id, symbol, day, ctime, price, _reason, _a, _i, cf = row

    lessons = ""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:active_lessons")
        if raw:
            lessons = f"{raw}\n\n"
    except Exception:
        pass

    started = time.monotonic()
    prompt = (lessons + "The gate DECLINED this setup. Should it have entered?\n"
              + json.dumps(dossier, default=str))
    reply = await llm_chat(prompt, system, temperature=0.1,
                           max_tokens=120, timeout=300.0)
    latency = int((time.monotonic() - started) * 1000)
    if reply is None:
        verdict, conf, reason = "no_llm", None, "LLM unavailable"
    else:
        verdict, conf, reason = _parse_verdict(reply)

    from sqlalchemy import text
    from app.database.postgres import engine
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO llm_rejection_reviews
              (decision_id, symbol, trade_date, candle_time, price, verdict,
               confidence, reason, model, latency_ms, dossier, cf_pnl_pct)
            VALUES (:did,:sym,:d,:ct,:px,:v,:c,:r,:m,:lat,:dos,:cf)
            ON CONFLICT (decision_id) DO NOTHING
        """), {
            "did": decision_id, "sym": symbol, "d": day, "ct": ctime,
            "px": price, "v": verdict, "c": conf, "r": reason,
            "m": active_model(), "lat": latency,
            "dos": json.dumps(dossier, default=str), "cf": cf,
        })
    return verdict


async def review_rejections(day: str | None = None, limit: int = _DAILY_CAP) -> dict:
    """Review a sample of NEAR-MISS rejections for `day` (default: the last
    completed trading day). Only rows that already carry a counterfactual label
    are sampled, so every verdict is scoreable the moment it is written.

    The sample is RANDOM, deliberately. Sampling by |cf_pnl| — the obvious
    "review the ones that mattered" instinct — silently destroys the
    scorecard: it selects on the outcome being reviewed, so `should_enter` and
    `agree_skip` are no longer being compared over the same distribution and
    the resulting edge means nothing. A random draw over near-misses is the
    only version of this that can honestly answer whether the model beats the
    gate.
    """
    from sqlalchemy import text
    from app.database.postgres import engine

    if day is None:
        now = datetime.now(IST)
        d = now.date()
        if not (d.weekday() < 5 and (now.hour * 60 + now.minute) >= (15 * 60 + 35)):
            d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        day = d.isoformat()

    await _ensure_table()
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            -- The trade date is NOT selected here: session_metadata.date is
            -- TEXT, so binding the same value as a date for the projection
            -- and as text for the comparison fights asyncpg's type inference
            -- for no benefit. We already know the day in Python.
            SELECT d.id, upper(d.symbol), d.candle_time, d.price,
                   d.reason, d.agents, d.indicators, d.cf_pnl_pct
            FROM session_decisions d
            LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
            WHERE d.executed = FALSE
              AND d.cf_pnl_pct IS NOT NULL
              AND d.candle_time < '13:00'
              AND COALESCE(sm.date,
                           (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) = :day
              AND (SELECT count(*) FROM jsonb_array_elements(d.agents) a
                   WHERE a->>'action' = 'BUY') >= :minbuy
              AND NOT EXISTS (SELECT 1 FROM llm_rejection_reviews r
                              WHERE r.decision_id = d.id)
            ORDER BY random()
            LIMIT :lim
        """), {"day": day, "minbuy": _MIN_BUY_VOTES,
               "lim": max(1, int(limit))})).fetchall()

    if not rows:
        return {"day": day, "reviewed": 0, "verdicts": {}}

    # Splice the trade date back in so downstream sees the same shape it would
    # have had from SQL: (id, symbol, day, candle_time, price, reason, agents,
    # indicators, cf_pnl_pct).
    day_date = datetime.strptime(day, "%Y-%m-%d").date()
    rows = [(r[0], r[1], day_date, *r[2:]) for r in rows]

    win_pct, avg_pnl = await _base_rates(day)
    system = _system_prompt(win_pct, avg_pnl)

    counts: dict[str, int] = {}
    for r in rows:
        try:
            v = await _review_one(r, system)
            counts[v] = counts.get(v, 0) + 1
        except Exception as exc:
            logger.warning("rejection review failed for decision %s: %s", r[0], exc)
    logger.info("LLM rejection review %s: %d reviewed %s", day, sum(counts.values()), counts,
                extra={"log_type": "ai_engine", "event": "llm_rejection_review",
                       "day": day, "reviewed": sum(counts.values())})
    return {"day": day, "reviewed": sum(counts.values()), "verdicts": counts}


async def rejection_review_report(days: int = 30) -> dict:
    """Is the reviewer any good? Compares what actually happened on the
    rejections it wanted to take against the ones it agreed to skip.

    `edge_pts` is the whole point: positive means the setups it flagged really
    did outperform the ones it passed on, so its judgement carries information
    the gate did not have. It earns influence over live entries only if this
    stays positive on a decent sample — the same bar every other gate rule had
    to clear.
    """
    from sqlalchemy import text
    from app.database.postgres import engine
    try:
        await _ensure_table()
        async with engine.begin() as conn:
            rows = (await conn.execute(text("""
                SELECT verdict, count(*)::int,
                       avg(cf_pnl_pct)::float,
                       (avg((cf_pnl_pct > 0)::int::float) * 100.0)::float
                FROM llm_rejection_reviews
                WHERE cf_pnl_pct IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => :d)
                GROUP BY 1
            """), {"d": days})).fetchall()
        by = {r[0]: {"n": r[1], "avg_cf_pnl_pct": round(r[2], 4),
                     "win_pct": round(r[3], 1)} for r in rows}
        enter, skip = by.get("should_enter"), by.get("agree_skip")
        edge = (round(enter["avg_cf_pnl_pct"] - skip["avg_cf_pnl_pct"], 4)
                if enter and skip else None)
        return {"by_verdict": by, "edge_pts": edge,
                "verdict": ("insufficient sample" if edge is None
                            else "informative" if edge > 0 else "no edge")}
    except Exception as exc:
        logger.warning("rejection_review_report failed: %s", exc)
        return {"by_verdict": {}, "edge_pts": None, "verdict": "error"}


async def _day_has_cf_labels(day: str | None) -> bool:
    """Has the counterfactual labeller reached `day` yet? Errs towards True so a
    DB hiccup cannot wedge the review loop into permanent deferral."""
    if not day:
        return True
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            row = (await conn.execute(text("""
                SELECT 1 FROM session_decisions d
                LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
                WHERE d.cf_labeled_at IS NOT NULL
                  AND COALESCE(sm.date,
                               (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) = :day
                LIMIT 1
            """), {"day": day})).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("cf-label readiness probe failed for %s: %s", day, exc)
        return True


async def rejection_review_loop() -> None:
    """Daily sweep, due from 04:00 IST — after counterfactual labelling has run,
    so the day's rejections already carry the outcomes this scores against.

    Due-time, not fire-time: 04:00 is inside the window the host is powered
    down, so the old sleep-to-the-hour form never fired. `nightly_loop` also
    serialises the catch-up, which keeps this behind the 03:00 post-mortems it
    is meant to follow. See app/utils/nightly.py."""
    from app.config import settings
    from app.utils.nightly import nightly_loop, NotReady
    if not getattr(settings, "LLM_REJECTION_REVIEW_ENABLED", True):
        logger.info("LLM rejection review disabled via config")
        return

    async def _run() -> object:
        res = await review_rejections(
            limit=int(getattr(settings, "LLM_REJECTION_REVIEW_CAP", _DAILY_CAP)))
        # Nothing reviewed can mean two very different things: the day really
        # had no near-miss rejections, or the counterfactual labeller has not
        # reached that day yet — it only sweeps outside market hours and only
        # touches days that are already over. Treating the second as success
        # burns the slot and the day is never reviewed (observed 2026-09-01:
        # the boot catch-up ran at 09:20 IST and returned reviewed=0 for
        # 2026-08-31, which had 1,368 decisions and 0 labels at that moment).
        if not res.get("reviewed") and not await _day_has_cf_labels(res.get("day")):
            raise NotReady(f"no counterfactual labels for {res.get('day')} yet")
        logger.info("LLM rejection review done: %s", res,
                    extra={"log_type": "ai_engine",
                           "event": "llm_rejection_review_done", **res})
        return res

    await nightly_loop("llm_rejection_review",
                       int(getattr(settings, "LLM_REJECTION_REVIEW_HOUR_IST", _SWEEP_HOUR_IST)),
                       _run, label="LLM rejection review")
