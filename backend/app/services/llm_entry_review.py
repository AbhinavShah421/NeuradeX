"""LLM entry-dossier reviewer — SHADOW MODE.

At the moment the gate approves a long entry, the full dossier (panel votes,
levels, indicators, gate context) is handed to the local LLM for an
approve/veto verdict. The verdict is LOGGED AND PERSISTED but NEVER acted on:
entries execute exactly as before. After a couple of weeks the stored verdicts
can be joined against realised/CF outcomes — the LLM earns veto power only if
its vetoes measurably separate losers from winners, the same evidence bar
every other gate rule had to clear (see 2026-07-07/08 factor screens).

Runs as a fire-and-forget task: the trading loop never waits on it. A slow or
dead LLM costs nothing but a missing shadow row.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

_DDL = """
CREATE TABLE IF NOT EXISTS llm_entry_reviews (
    id           SERIAL PRIMARY KEY,
    session_id   TEXT,
    symbol       TEXT NOT NULL,
    trade_date   DATE NOT NULL,
    candle_time  TEXT NOT NULL,
    price        DOUBLE PRECISION,
    verdict      TEXT,            -- approve | veto | parse_error | no_llm
    confidence   DOUBLE PRECISION,
    reason       TEXT,
    model        TEXT,
    latency_ms   INTEGER,
    dossier      JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, trade_date, candle_time)
);
"""
_ddl_ready = False

_SYSTEM = (
    "You are the final risk reviewer on an intraday long-only trading desk. "
    "You receive the entry dossier a rules-based system already approved, and "
    "PRECEDENTS: the most similar setups this desk has actually traded, with "
    "their realised outcomes. Reason case by case over those precedents. "
    "Two cautions about them: the precedent bank's aggregate win-rate has been "
    "measured to carry little predictive power, so treat precedents as context "
    "rather than proof; and NO precedent means NO evidence, which is not the "
    "same as no risk — never approve merely because nothing similar is shown. "
    "Judge whether this specific entry is sound. Reply with ONLY a JSON "
    'object: {"verdict": "approve" or "veto", "confidence": 0.0-1.0, '
    '"reason": "<one sentence>"}'
)

# How many precedents to put in front of the reviewer. Small on purpose: an 8B
# model reasoning case-by-case degrades into summarising once the list gets
# long, and every case costs prefill on CPU.
_PRECEDENT_K = int(os.getenv("LLM_REVIEW_PRECEDENT_K", "10"))


def _build_dossier(symbol: str, candle: dict, agents: list[dict], ind: dict,
                   gate_label: str, session: dict) -> dict:
    """Compact, LLM-readable snapshot of everything the gate saw."""
    votes = [{"agent": a.get("agent_name"), "vote": a.get("action"),
              "conf": round(float(a.get("confidence") or 0), 2)}
             for a in agents]
    ds = next((a for a in agents if a.get("agent_name") == "day_structure"), None)
    ind_ds = (ds or {}).get("indicators") or {}
    return {
        "symbol": symbol,
        "time": candle.get("time"),
        # The day being TRADED (replay/backtest sessions trade a past date).
        "trade_date": session.get("date"),
        "price": candle.get("close"),
        "gate": gate_label,
        "mode": session.get("mode", "paper"),
        "votes": votes,
        "indicators": {k: ind.get(k) for k in
                       ("rsi", "vwap", "sma5", "sma20", "momentum_pct", "atr")},
        "levels": {"resistances": ind_ds.get("levels_res"),
                   "supports": ind_ds.get("levels_sup"),
                   "day_range_pct": ind_ds.get("day_range_pct"),
                   "rr_ratio": ind_ds.get("rr_ratio")},
    }


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
        if v not in ("approve", "veto"):
            return "parse_error", None, reply[:300]
        conf = d.get("confidence")
        conf = float(conf) if conf is not None else None
        return v, conf, str(d.get("reason", ""))[:300]
    except Exception:
        return "parse_error", None, (reply or "")[:300]


def _as_date(value):
    """Coerce a session's trade_date to a real date for the DATE column.

    Sessions carry it as an ISO *string*; asyncpg binds DATE strictly and
    rejects one with "'str' object has no attribute 'toordinal'". The insert
    used to receive datetime.now().date() because the dossier had no
    trade_date at all — the day that field was added (so replay sessions stop
    being stamped with the wall clock) every paper review began failing to
    persist, silently: the "LLM shadow review -> approve" success line still
    logs, and only a WARNING underneath says nothing was written. That cost
    ~10 days of shadow data before anyone noticed.
    """
    if value is None or isinstance(value, datetime):
        return value.date() if isinstance(value, datetime) else None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


async def _retrieve_precedents(fingerprint, symbol: str, regime: str | None) -> dict:
    """The nearest past setups to this one, plus their weighted aggregate.

    Retrieval is conditioned on THIS entry's fingerprint. The reviewer
    previously saw only `ai_engine:active_lessons` — the same global top-8
    failure modes on every single decision, identical for a breakout and a
    mean-reversion entry. Those lessons are still included (they encode
    post-mortem reasoning that no single neighbour carries); this adds the
    specific precedent the global blob cannot express.

    Returns {} when the bank has no genuinely similar case, so the caller can
    tell "no evidence" apart from "evidence of safety".
    """
    if not fingerprint:
        return {}
    try:
        # The shared singleton, not a fresh PatternMemory: it owns the cached
        # k-NN matrix, and a private instance would reload all ~10k rows from
        # Postgres on every single review.
        from app.agents import get_memory
        cases = await get_memory().retrieve_cases(
            list(fingerprint), symbol=symbol, regime=regime, k=_PRECEDENT_K)
    except Exception as exc:
        logger.debug("precedent retrieval failed for %s: %s", symbol, exc)
        return {}
    if not cases:
        return {}

    # Aggregate with the same evidence weights the memory gate uses, so the
    # summary the reviewer reads cannot disagree with the gate's own arithmetic.
    w_total = sum(c["evidence_weight"] for c in cases) or 1e-9
    wins = sum(c["evidence_weight"] for c in cases if c["pnl_pct"] > 0)
    avg_pnl = sum(c["pnl_pct"] * c["evidence_weight"] for c in cases) / w_total
    return {
        "cases": cases,
        "summary": {
            "n": len(cases),
            "weighted_win_rate": round(wins / w_total, 3),
            "weighted_avg_pnl_pct": round(avg_pnl, 3),
            "min_similarity": min(c["similarity"] for c in cases),
            "symbol_local": cases[0].get("symbol_local", False),
            "similarity_floor": cases[0].get("floor"),
        },
    }


def _precedent_block(pre: dict) -> str:
    """Render precedents for the prompt. Explicit about absence."""
    if not pre:
        return ("PRECEDENTS: none — no past setup is similar enough to inform this "
                "one. That is an absence of evidence, not evidence of safety.\n\n")
    s = pre["summary"]
    lines = [
        f"PRECEDENTS — {s['n']} most similar setups this desk actually traded "
        f"(sim 0-1; w = evidence weight, low w = unreliable source; pnl realised %):"
    ]
    for c in pre["cases"]:
        lines.append(
            f"- {c['symbol']:<12} sim {c['similarity']:.2f}  {c['action']:<4} "
            f"{c['outcome']:<4} {c['pnl_pct']:+.2f}%  w{c['evidence_weight']}  {c['source']}"
        )
    lines.append(
        f"Aggregate: weighted win rate {s['weighted_win_rate']:.0%}, "
        f"avg {s['weighted_avg_pnl_pct']:+.2f}%"
        f"{', same-symbol history' if s['symbol_local'] else ''}."
    )
    return "\n".join(lines) + "\n\n"


async def _review(dossier: dict, session_id: str | None,
                  fingerprint=None, regime: str | None = None) -> None:
    from app.utils.llm_client import llm_chat, active_model
    started = time.monotonic()
    # Retrieval conditioned on this entry. Stored on the dossier (persisted as
    # JSONB) so a later scorecard can ask the question that decides whether any
    # of this earns a vote: did verdicts move with precedent, and did that
    # movement track realised outcome?
    precedents = await _retrieve_precedents(fingerprint, dossier["symbol"], regime)
    dossier["precedents"] = precedents.get("summary") or None
    dossier["precedent_cases"] = precedents.get("cases") or []
    # Prepend the distilled lessons from past losing trades (post-mortem loop,
    # ai_engine:active_lessons) so the reviewer judges each entry against the
    # system's own recorded failure modes — e.g. "chasing momentum into
    # resistance (12x)". Same cache the AIEngine manual analysis consults.
    lessons = ""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:active_lessons")
        if raw:
            lessons = f"{raw}\n\n"
    except Exception:
        pass
    # The raw case list goes in the prompt as the rendered block, not as JSON —
    # keep it out of the dossier copy the model reads or it sees every
    # precedent twice.
    dossier_for_prompt = {k: v for k, v in dossier.items() if k != "precedent_cases"}
    prompt = (lessons + _precedent_block(precedents)
              + "Entry dossier:\n" + json.dumps(dossier_for_prompt, default=str))
    # Generous budget: 8B on CPU needs ~40s model reload after idle plus
    # prefill+generation. Shadow reviews are async — latency costs nothing.
    reply = await llm_chat(prompt, _SYSTEM, temperature=0.1,
                           max_tokens=120, timeout=300.0)
    latency = int((time.monotonic() - started) * 1000)
    if reply is None:
        verdict, conf, reason = "no_llm", None, "LLM unavailable"
    else:
        verdict, conf, reason = _parse_verdict(reply)

    try:
        await _ensure_table()
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO llm_entry_reviews
                  (session_id, symbol, trade_date, candle_time, price, verdict,
                   confidence, reason, model, latency_ms, dossier)
                VALUES (:sid,:sym,:d,:ct,:px,:v,:c,:r,:m,:lat,:dos)
                ON CONFLICT (symbol, trade_date, candle_time) DO NOTHING
            """), {
                # The SESSION's trading date, not the server's today: a replay
                # or backtest session reviews a historical bar, and stamping it
                # with the wall-clock date silently detaches the verdict from
                # the decision it judged (the join to outcomes then finds
                # nothing). Live/paper sessions are unaffected — for them the
                # two are the same day.
                "sid": session_id, "sym": dossier["symbol"],
                "d": _as_date(dossier.get("trade_date")) or datetime.now(_IST).date(),
                "ct": dossier.get("time"),
                "px": dossier.get("price"), "v": verdict, "c": conf,
                "r": reason, "m": active_model(), "lat": latency,
                "dos": json.dumps(dossier, default=str),
            })
        persisted = True
    except Exception as exc:
        persisted = False
        logger.warning("llm entry review persist failed: %s", exc)

    # Say whether the row actually landed. This line used to read the same
    # either way, so a reviewer that had persisted nothing for ten days looked
    # exactly like one that was working — the whole point of shadow mode is the
    # stored row, not the verdict scrolling past in the log.
    logger.info("LLM shadow review: %s %s -> %s (%s)",
                dossier["symbol"], dossier.get("time"), verdict,
                "stored" if persisted else "NOT STORED",
                extra={"log_type": "ai_engine", "event": "llm_entry_review",
                       "symbol": dossier["symbol"], "verdict": verdict,
                       "confidence": conf, "latency_ms": latency,
                       "persisted": persisted})


# ── Scorecard ─────────────────────────────────────────────────────────────────

_SCORECARD_SQL = """
SELECT r.verdict, r.confidence,
       (r.dossier->'precedents'->>'weighted_win_rate')::float   AS prec_win_rate,
       (r.dossier->'precedents'->>'weighted_avg_pnl_pct')::float AS prec_avg_pnl,
       (r.dossier->'precedents'->>'n')::int                      AS prec_n,
       t.pnl_pct
FROM llm_entry_reviews r
JOIN trade_records t
  ON t.session_id = r.session_id AND upper(t.symbol) = upper(r.symbol)
WHERE t.pnl_pct IS NOT NULL
  AND r.created_at >= NOW() - make_interval(days => :days)
"""


def _corr(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r without pulling in scipy. None when undefined."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / (sxx * syy) ** 0.5, 4)


def _group(rows: list[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        out.setdefault(k, []).append(r["pnl_pct"])
    return {
        str(k): {
            "n": len(v),
            "win_rate": round(sum(1 for p in v if p > 0) / len(v), 3),
            "avg_pnl_pct": round(sum(v) / len(v), 4),
        }
        for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))
    }


async def review_scorecard(days: int = 90) -> dict:
    """Has the shadow reviewer earned a vote yet?

    Answers three separate questions, because they can fail independently:
      1. Does the VERDICT separate winners from losers? (the stated bar)
      2. Does CONFIDENCE track realised outcome? (the only channel measured
         to respond to evidence at all — see the 2026-08-10 sensitivity test)
      3. Does the retrieved PRECEDENT win-rate track realised outcome? This
         scores the retrieval itself, independently of what the LLM did with it.

    trade_records.pnl_pct is a FRACTION (-0.0045 = -0.45%); precedent P&L is
    already percent. Both are normalised to percent here so the two columns are
    actually comparable — mixing the scales is what once made every aggregated
    lesson read "avg -0.0%".
    """
    from sqlalchemy import text
    from app.database.postgres import engine

    try:
        await _ensure_table()
        async with engine.begin() as conn:
            raw = (await conn.execute(text(_SCORECARD_SQL), {"days": days})).fetchall()
    except Exception as exc:
        logger.warning("review scorecard query failed: %s", exc)
        return {"error": str(exc)[:200], "sample": 0}

    rows = [{
        "verdict": r[0], "confidence": r[1],
        "prec_win_rate": r[2], "prec_avg_pnl": r[3], "prec_n": r[4],
        "pnl_pct": float(r[5]) * 100.0,     # fraction -> percent
    } for r in raw]

    if not rows:
        return {"sample": 0, "verdict": "no data",
                "note": f"no reviews joined to a closed trade in the last {days} days"}

    with_prec = [r for r in rows if r["prec_win_rate"] is not None]
    conf_rows = [r for r in rows if r["confidence"] is not None]

    approve = [r["pnl_pct"] for r in rows if r["verdict"] == "approve"]
    veto = [r["pnl_pct"] for r in rows if r["verdict"] == "veto"]
    separation = (round(sum(approve) / len(approve) - sum(veto) / len(veto), 4)
                  if approve and veto else None)

    return {
        "sample": len(rows),
        "window_days": days,
        "by_verdict": _group(rows, lambda r: r["verdict"]),
        # The bar the module set itself: vetoes must land on worse trades than
        # approvals. Positive = the LLM's vetoes were the worse trades.
        "verdict_separation_pct": separation,
        "confidence": {
            "n": len(conf_rows),
            "corr_with_pnl": _corr([r["confidence"] for r in conf_rows],
                                   [r["pnl_pct"] for r in conf_rows]),
            "by_bucket": _group(conf_rows,
                                lambda r: f"{int(r['confidence'] * 10) / 10:.1f}"),
        },
        "precedents": {
            "n": len(with_prec),
            "coverage": round(len(with_prec) / len(rows), 3),
            "corr_win_rate_with_pnl": _corr([r["prec_win_rate"] for r in with_prec],
                                            [r["pnl_pct"] for r in with_prec]),
            "corr_avg_pnl_with_pnl": _corr(
                [r["prec_avg_pnl"] for r in with_prec if r["prec_avg_pnl"] is not None],
                [r["pnl_pct"] for r in with_prec if r["prec_avg_pnl"] is not None]),
        },
        # Deliberately not a pass/fail: 40-odd samples cannot settle this, and
        # reporting a verdict at this n is how a noise result becomes policy.
        "readiness": ("insufficient — need a few hundred joined reviews"
                      if len(rows) < 200 else "sufficient sample; judge separation"),
    }


def shadow_review_entry(symbol: str, candle: dict, agents: list[dict],
                        ind: dict, gate_label: str, session: dict,
                        fingerprint=None, regime: str | None = None) -> None:
    """Fire-and-forget shadow review. Never blocks, never raises.

    fingerprint/regime are the ones computed for THIS entry — passed in rather
    than recomputed so the precedents retrieved are the neighbours of the exact
    vector the gate scored.
    """
    try:
        dossier = _build_dossier(symbol, candle, agents, ind, gate_label, session)
        asyncio.get_running_loop().create_task(
            _review(dossier, session.get("id"), fingerprint, regime))
    except Exception as exc:
        logger.debug("shadow review not scheduled: %s", exc)
