"""Live architecture monitor — on-demand, fault-first system map.

Purpose is diagnosis, not decoration: every failure this project has actually hit
looked healthy from the outside. A container can be "Up" while the loop inside it
has not run since July; a RabbitMQ queue can exist with zero consumers; a model
can be registered and served while predicting a constant. So this module never
infers health from "the process is running" — it PROBES:

  * datastores get a real round-trip (SELECT 1 / PING / cluster health)
  * queues are checked for CONSUMERS, not just existence
  * learning loops are judged on how long since they last wrote something

Resource discipline: the full sweep is expensive (Docker stats over ~30
containers, plus a dozen network probes), so it is gated behind an explicit
session. `/snapshot` returns `{"active": false}` unless a session is armed. The
UI arms one on open, heartbeats while the page lives, and disarms on close; the
Redis TTL means a crashed browser or closed laptop stops the sweep on its own
rather than leaving it running forever.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter

from app.config import settings
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

IST = timezone(timedelta(hours=5, minutes=30))

_GITHUB_BASE = "https://github.com/AbhinavShah421/NeuradeX/blob/main"

_SESSION_KEY = "monitor:session:active"
_SESSION_TTL = 90          # seconds; UI heartbeats every ~30s
_PROBE_TIMEOUT = 3.0       # per-probe ceiling so one dead host can't stall the sweep

# ── Topology ─────────────────────────────────────────────────────────────────
# Layers render left→right in the UI. `probe` names the health check to run.
# `container` links a node to its Docker container so state/logs/CPU attach.
_LAYERS = ["edge", "ui", "api", "vote", "agents", "decision", "execution", "data", "ml"]

# The 12 agents the BACKEND ensemble actually votes with. These are Python
# classes inside the backend/session-runner process — they have no container of
# their own, which is why a container-only map showed 7 "agents" while
# ai_engine_agent_weights tracked 12. The duplicate technical/pattern/macro/rl
# microservices were deleted 2026-08-18; only sentiment-agent remains as a
# container, and solely because the backend calls it for FinBERT inference.
# These in-process agents produce session_decisions AND now feed the execution
# chain via app/utils/decision_publisher.py. Weight and accuracy attached live.
_VOTE_AGENTS: list[tuple[str, str, str]] = [
    ("technical",     "Technical",     "backend/app/agents/technical.py"),
    ("pattern",       "Pattern",       "backend/app/agents/pattern.py"),
    ("momentum",      "Momentum",      "backend/app/agents/momentum.py"),
    ("volatility",    "Volatility",    "backend/app/agents/volatility.py"),
    ("sentiment",     "Sentiment",     "backend/app/agents/sentiment.py"),
    ("rl",            "RL (Q-table)",  "backend/app/agents/rl_agent.py"),
    ("memory",        "Pattern Memory", "backend/app/agents/memory.py"),
    ("meanrev",       "Mean-Reversion", "backend/app/agents/meanrev.py"),
    ("regime",        "Regime Filter", "backend/app/agents/regime.py"),
    ("anomaly",       "Anomaly (veto)", "backend/app/agents/anomaly.py"),
    ("gbm",           "GBM P(up)",     "backend/app/agents/gbm_agent.py"),
    ("day_structure", "Day Structure", "backend/app/agents/day_structure.py"),
]

_NODES: list[dict] = [
    {"id": "ngrok",      "label": "ngrok",           "layer": "edge",      "container": "stock-prediction-ngrok"},
    {"id": "nginx",      "label": "Nginx",           "layer": "edge",      "container": "stock-prediction-nginx",      "probe": "http", "url": "http://nginx:80/"},
    {"id": "frontend",   "label": "Frontend",        "layer": "ui",        "container": "stock-prediction-frontend",   "probe": "http", "url": "http://frontend:3000/"},
    {"id": "backend",    "label": "Backend API",     "layer": "api",       "container": "stock-prediction-backend",    "probe": "self"},
    {"id": "runner",     "label": "Session Runner",  "layer": "api",       "container": "stock-prediction-session-runner"},
    {"id": "market",     "label": "Market Data",     "layer": "api",       "container": "stock-prediction-market-data",   "probe": "http", "url": "http://market-data-service:8001/health"},
    {"id": "groww",      "label": "Groww Feed",      "layer": "api",       "container": "stock-prediction-groww-feed"},
    {"id": "sentiment",  "label": "Sentiment Agent", "layer": "agents",    "container": "stock-prediction-sentiment-agent", "probe": "http", "url": "http://sentiment-agent:8003/health"},
    {"id": "scanner",    "label": "Stock Scanner",   "layer": "agents",    "container": "stock-prediction-stock-scanner",   "probe": "http", "url": "http://stock-scanner:8014/health"},
    {"id": "sentsvc",    "label": "Sentiment Svc",   "layer": "agents",    "container": "stock-prediction-sentiment",       "probe": "http", "url": "http://sentiment-service:8016/health"},

    {"id": "ensemble",   "label": "Ensemble Engine", "layer": "decision",  "container": "stock-prediction-ensemble-engine", "probe": "http", "url": "http://ensemble-engine:8007/health"},
    {"id": "autopilot",  "label": "Autopilot",       "layer": "decision",  "container": "stock-prediction-autopilot",       "probe": "http", "url": "http://autopilot-service:8015/health"},
    {"id": "feedback",   "label": "Feedback Svc",    "layer": "decision",  "container": "stock-prediction-feedback-service","probe": "http", "url": "http://feedback-service:8012/health"},

    {"id": "risk",       "label": "Risk Engine",     "layer": "execution", "container": "stock-prediction-risk-engine",     "probe": "http", "url": "http://risk-engine:8010/health"},
    {"id": "executor",   "label": "Trade Executor",  "layer": "execution", "container": "stock-prediction-trade-executor"},

    {"id": "postgres",   "label": "PostgreSQL",      "layer": "data",      "container": "stock-prediction-postgres",   "probe": "postgres"},
    {"id": "redis",      "label": "Redis",           "layer": "data",      "container": "stock-prediction-redis",      "probe": "redis"},
    {"id": "rabbitmq",   "label": "RabbitMQ",        "layer": "data",      "container": "stock-prediction-rabbitmq",   "probe": "rabbitmq"},
    {"id": "elastic",    "label": "Elasticsearch",   "layer": "data",      "container": "stock-prediction-elasticsearch", "probe": "elastic"},

    {"id": "trainer",    "label": "Model Trainer",   "layer": "ml",        "container": "stock-prediction-model-trainer"},
    {"id": "mlflow",     "label": "MLflow",          "layer": "ml",        "container": "stock-prediction-mlflow",     "probe": "http", "url": "http://mlflow:5000/health"},
    {"id": "ollama",     "label": "Ollama LLM",      "layer": "ml",        "container": "stock-prediction-ollama",     "probe": "http", "url": "http://ollama:11434/"},
    {"id": "kibana",     "label": "Kibana",          "layer": "ml",        "container": "stock-prediction-kibana"},
    {"id": "filebeat",   "label": "Filebeat",        "layer": "ml",        "container": "stock-prediction-filebeat"},
    {"id": "docs",       "label": "Docs Site",       "layer": "ml",        "container": "stock-prediction-docs",    "probe": "http", "url": "http://docs:3001/"},
    {"id": "adminer",    "label": "Adminer (DB)",    "layer": "ml",        "container": "stock-prediction-adminer", "probe": "http", "url": "http://adminer:8080/"},
] + [
    {"id": f"vote_{aid}", "label": label, "layer": "vote",
     "kind": "inprocess", "agent": aid, "source": src}
    for aid, label, src in _VOTE_AGENTS
]

# Edges are the end-to-end flow. `queue` marks an AMQP hop whose CONSUMER COUNT
# is checked — a queue with zero consumers is a silently broken link and is
# exactly how trade.outcomes.rl went unnoticed.
_EDGES: list[dict] = [
    {"from": "ngrok",     "to": "nginx",     "kind": "http"},
    {"from": "nginx",     "to": "frontend",  "kind": "http"},
    {"from": "nginx",     "to": "backend",   "kind": "http"},
    {"from": "frontend",  "to": "backend",   "kind": "http"},
    {"from": "backend",   "to": "postgres",  "kind": "sql"},
    {"from": "backend",   "to": "redis",     "kind": "cache"},
    {"from": "backend",   "to": "rabbitmq",  "kind": "amqp"},
    {"from": "runner",    "to": "postgres",  "kind": "sql"},
    {"from": "runner",    "to": "redis",     "kind": "cache"},
    {"from": "groww",     "to": "runner",    "kind": "ws"},
    {"from": "market",    "to": "rabbitmq",  "kind": "amqp"},
    {"from": "market",    "to": "sentiment", "kind": "amqp", "queue": "market.data.sentiment"},
    {"from": "sentiment", "to": "ensemble",  "kind": "amqp"},
    # The backend ensemble now supplies the decision (decision_publisher.py,
    # flag-gated). ensemble-engine applies the MLflow meta-gate + calibration
    # and forwards to risk — it no longer aggregates raw agent signals.
    {"from": "backend",   "to": "ensemble",  "kind": "amqp", "queue": "ensemble.decision"},
    {"from": "ensemble",  "to": "risk",      "kind": "amqp", "queue": "ensemble.decision"},
    {"from": "risk",      "to": "executor",  "kind": "amqp", "queue": "risk.validated"},
    {"from": "executor",  "to": "feedback",  "kind": "amqp", "queue": "trade.outcomes.feedback"},
    # RL learns from outcomes through the backend Q-table, not a queue — the
    # `trade.outcomes.rl` binding was removed 2026-08-17 because no consumer for
    # it was ever written (see market-data-service rabbitmq_setup.py).
    {"from": "backend",   "to": "rl",        "kind": "state"},
    {"from": "backend",   "to": "feedback",  "kind": "http"},
    {"from": "feedback",  "to": "postgres",  "kind": "sql"},
    {"from": "ensemble",  "to": "mlflow",    "kind": "http"},
    {"from": "trainer",   "to": "mlflow",    "kind": "http"},
    {"from": "trainer",   "to": "postgres",  "kind": "sql"},
    {"from": "scanner",   "to": "postgres",  "kind": "sql"},
    {"from": "autopilot", "to": "backend",   "kind": "http"},
    {"from": "backend",   "to": "ollama",    "kind": "http"},
    {"from": "backend",   "to": "elastic",   "kind": "http"},
    {"from": "elastic",   "to": "kibana",    "kind": "http"},
    # Filebeat tails every container's json-log file and ships to Elasticsearch,
    # which is how the 15 services that never used elk_logger became searchable.
    {"from": "filebeat",  "to": "elastic",   "kind": "http"},
    {"from": "nginx",     "to": "docs",      "kind": "http"},
    {"from": "nginx",     "to": "adminer",   "kind": "http"},
    {"from": "adminer",   "to": "postgres",  "kind": "sql"},
    {"from": "sentsvc",   "to": "postgres",  "kind": "sql"},
] + [
    # The session runner invokes each in-process agent every bar, then the
    # backend ensemble combines their votes. Drawn as invocation only — a
    # return edge per agent would double the line count for no new information.
    {"from": "runner", "to": f"vote_{aid}", "kind": "call"}
    for aid, _, _ in _VOTE_AGENTS
]


# ── Session gating ───────────────────────────────────────────────────────────

async def _session_active() -> bool:
    try:
        from app.utils.redis_client import cache_get
        return bool(await cache_get(_SESSION_KEY))
    except Exception:
        return False


@router.post("/session/start")
async def session_start():
    from app.utils.redis_client import cache_set
    await cache_set(_SESSION_KEY, str(time.time()), expire=_SESSION_TTL)
    logger.info("monitor session armed", extra={"log_type": "monitor", "event": "session_start"})
    return {"active": True, "ttl": _SESSION_TTL}


@router.post("/session/heartbeat")
async def session_heartbeat():
    from app.utils.redis_client import cache_set
    await cache_set(_SESSION_KEY, str(time.time()), expire=_SESSION_TTL)
    return {"active": True, "ttl": _SESSION_TTL}


@router.post("/session/stop")
async def session_stop():
    from app.utils.redis_client import cache_delete
    await cache_delete(_SESSION_KEY)
    logger.info("monitor session disarmed", extra={"log_type": "monitor", "event": "session_stop"})
    return {"active": False}


@router.get("/session")
async def session_state():
    return {"active": await _session_active(), "ttl": _SESSION_TTL}


# ── Probes ───────────────────────────────────────────────────────────────────

async def _probe_http(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get(url)
        # Any answer proves reachability; 4xx still means the process is serving.
        ok = r.status_code < 500
        return {"ok": ok, "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"ok": False, "detail": type(exc).__name__}


async def _probe_postgres() -> dict:
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True, "detail": "SELECT 1 ok"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:120]}


async def _probe_redis() -> dict:
    try:
        from app.utils.redis_client import get_redis
        await get_redis().ping()
        return {"ok": True, "detail": "PING ok"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:120]}


async def _probe_elastic() -> dict:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get("http://elasticsearch:9200/_cluster/health")
            r.raise_for_status()
            js = r.json()
        status = js.get("status", "unknown")
        return {"ok": status in ("green", "yellow"), "detail": f"cluster {status}"}
    except Exception as exc:
        return {"ok": False, "detail": type(exc).__name__}


async def _rabbit_queues() -> dict:
    """Queue → consumer/message counts via the management API. Consumers are the
    signal that matters: a durable queue with zero consumers accepts publishes
    and silently drops the work on the floor."""
    try:
        auth = (settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, auth=auth) as c:
            r = await c.get(f"http://{settings.RABBITMQ_HOST}:15672/api/queues")
            r.raise_for_status()
            qs = r.json()
        return {
            q["name"]: {
                "consumers": q.get("consumers", 0),
                "messages": q.get("messages", 0),
            }
            for q in qs
        }
    except Exception as exc:
        logger.debug("rabbit queue probe failed: %s", exc)
        return {}


async def _probe_rabbitmq() -> dict:
    qs = await _rabbit_queues()
    if not qs:
        return {"ok": False, "detail": "management API unreachable"}
    starved = [n for n, v in qs.items() if v["consumers"] == 0]
    return {
        "ok": True,
        "detail": f"{len(qs)} queues, {len(starved)} with no consumer",
        "queues": qs,
    }


# ── Learning loops ───────────────────────────────────────────────────────────

def _age_hours(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except Exception:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _loop(name: str, label: str, age_h: float | None, stale_h: float,
          detail: str, why: str) -> dict:
    """Grade one loop by how long since it last did work.

    Staleness is the right test because these loops fail SILENTLY — nothing
    throws when a scheduled trainer simply never fires. `age_h is None` means it
    has never run at all, which is worse than stale, not better."""
    if age_h is None:
        status = "broken"
    elif age_h > stale_h * 3:
        status = "broken"
    elif age_h > stale_h:
        status = "stale"
    else:
        status = "ok"
    return {
        "id": name, "label": label, "status": status,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "threshold_hours": stale_h, "detail": detail, "why": why,
    }


async def _agent_stats() -> dict:
    """Live weight + measured per-action accuracy for each in-process agent.

    Weights come from ai_engine_agent_weights; the action rates come from the
    Redis table the ensemble actually reads. Showing both together is the point:
    it is how the inverted weighting was spotted (highest weight sitting on the
    worst BUY accuracy).
    """
    out: dict[str, dict] = {}
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT agent_name, weight, total_predictions, correct_predictions "
                "FROM ai_engine_agent_weights"
            ))).fetchall()
        for r in rows:
            out[r[0]] = {"weight": round(float(r[1]), 3),
                         "predictions": int(r[2] or 0),
                         "correct": int(r[3] or 0)}
    except Exception as exc:
        logger.debug("agent weight read failed: %s", exc)

    try:
        import json as _json
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:agent_action_rates")
        if raw:
            payload = _json.loads(raw)
            base = payload.get("base", {})
            for agent, rates in (payload.get("rates") or {}).items():
                e = out.setdefault(agent, {})
                e["buyRate"] = rates.get("BUY")
                e["baseRate"] = base.get("BUY")
                if rates.get("BUY") is not None and base.get("BUY") is not None:
                    e["lift"] = round(rates["BUY"] - base["BUY"], 3)
    except Exception as exc:
        logger.debug("agent action rates read failed: %s", exc)
    return out


async def _ensemble_engine_health() -> dict | None:
    """Is the microservice (Orders) pipeline capable of ever trading?

    It ran for its entire life emitting 100% HOLD without anyone noticing,
    because "container up, queues flowing, agents responding" all looked healthy.
    The cause is arithmetic: `aggregate_signals` divides the winning side's
    weighted confidence by the TOTAL weight, so HOLD votes dilute every
    directional score. Observed ceiling 0.560 against MIN_CONFIDENCE_TO_TRADE
    0.60 — no decision could ever clear the gate.

    Comparing the observed confidence ceiling against the gate turns that from an
    invisible dead end into a stated fault.
    """
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get("http://ensemble-engine:8007/decisions/recent")
            r.raise_for_status()
            raw = r.json()
    except Exception as exc:
        logger.debug("ensemble-engine decision probe failed: %s", exc)
        return None

    rows = raw if isinstance(raw, list) else raw.get("decisions", raw.get("data", []))
    payloads = [x.get("payload", x) for x in rows if isinstance(x, dict)]
    if not payloads:
        return None

    actions = [p.get("final_action") for p in payloads if p.get("final_action")]
    confs = [p.get("weighted_confidence") for p in payloads
             if isinstance(p.get("weighted_confidence"), (int, float))]
    if not actions:
        return None

    gate = 0.60          # MIN_CONFIDENCE_TO_TRADE in ensemble-engine
    ceiling = max(confs) if confs else None
    hold_pct = 100.0 * sum(1 for a in actions if a == "HOLD") / len(actions)
    return {
        "sample": len(actions),
        "holdPct": round(hold_pct, 1),
        "confCeiling": ceiling,
        "gate": gate,
        "inert": bool(ceiling is not None and ceiling < gate and hold_pct >= 99.0),
    }


async def _learning_loops() -> list[dict]:
    """Health of every learning loop. Thresholds follow each loop's real cadence:
    nightly trainers get 48h, per-trade loops get a trading-week."""
    out: list[dict] = []
    try:
        from sqlalchemy import text
        from app.database.postgres import engine
        async with engine.begin() as conn:
            aw = (await conn.execute(text(
                "SELECT max(updated_at), COALESCE(sum(total_count),0) FROM agent_weights"
            ))).fetchone()
            ai = (await conn.execute(text(
                "SELECT max(updated_at), COALESCE(sum(total_predictions),0) FROM ai_engine_agent_weights"
            ))).fetchone()
            pm = (await conn.execute(text(
                "SELECT updated_at, n_samples FROM pattern_model_state WHERE id=1"
            ))).fetchone()
            gbm = (await conn.execute(text(
                "SELECT max(updated_at) FROM gbm_model_state"
            ))).fetchone()
            cf = (await conn.execute(text(
                "SELECT max(cf_labeled_at) FROM session_decisions"
            ))).fetchone()
            dec = (await conn.execute(text(
                "SELECT max(created_at) FROM session_decisions"
            ))).fetchone()
            mem = (await conn.execute(text(
                "SELECT max(created_at) FROM pattern_memory"
            ))).fetchone()
    except Exception as exc:
        logger.warning("learning loop probe failed: %s", exc)
        return [{"id": "loops", "label": "Learning loops", "status": "broken",
                 "age_hours": None, "threshold_hours": 0,
                 "detail": f"probe failed: {exc}", "why": "Could not read Postgres."}]

    out.append(_loop(
        "agent_weights", "Ensemble weights (legacy)", _age_hours(aw[0] if aw else None), 168,
        f"{int(aw[1]) if aw else 0} scored votes recorded",
        "Feeds the ensemble-engine vote. Advances when a PAPER/LIVE trade closes.",
    ))
    out.append(_loop(
        "ai_engine_weights", "Agent weights (AI engine)", _age_hours(ai[0] if ai else None), 168,
        f"{int(ai[1]) if ai else 0} predictions counted",
        "Per-agent weights for the backend ensemble; updated on every recorded outcome.",
    ))
    out.append(_loop(
        "pattern_model", "Pattern model (online)", _age_hours(pm[0] if pm else None), 48,
        f"{int(pm[1]):,} samples" if pm and pm[1] else "no samples",
        "Nightly retrain at 01:00 IST. Froze for five weeks when its only trigger was disabled.",
    ))
    out.append(_loop(
        "gbm", "Gradient-boosted P(up)", _age_hours(gbm[0] if gbm else None), 48,
        "nightly retrain",
        "Nightly GBM retrain at 03:00 IST, daily + intraday slots.",
    ))
    # 96h, not 48h: labelling only runs on COMPLETED trading days, so the gap
    # from Friday evening to Monday evening is legitimately ~72h with nothing
    # wrong. A 48h limit raised a false "stale" every Monday — and a warning
    # that fires on a healthy weekend teaches you to ignore the panel.
    out.append(_loop(
        "counterfactual", "Counterfactual labelling", _age_hours(cf[0] if cf else None), 96,
        "labels the decisions the gates declined (completed days only)",
        "Off-hours sweep; feeds action-rates, RL and pattern memory. "
        "Today's decisions are labelled once the day closes.",
    ))
    out.append(_loop(
        "pattern_memory", "Pattern memory bank", _age_hours(mem[0] if mem else None), 168,
        "case bank for the evidence gate",
        "Grows from closed trades and counterfactual near-misses.",
    ))
    out.append(_loop(
        "decisions", "Decision flow", _age_hours(dec[0] if dec else None), 24,
        "session decisions written",
        "The runner should write decisions on every bar during market hours.",
    ))

    # Three nightly loops had no panel entry at all, so when they stopped on
    # 2026-08-25 nothing said so — the loss post-mortems in particular went a
    # full week writing zero rows while the entry prompts kept reading a stale
    # lessons cache. nightly_run_state is written only on a successful run, so
    # its age is the honest "when did this last do something" number.
    try:
        from app.utils.nightly import states as _nightly_states
        ns = await _nightly_states()
    except Exception as exc:
        logger.debug("nightly state probe failed: %s", exc)
        ns = {}

    for key, label, why in (
        ("loss_learning", "Loss post-mortems",
         "Explains each losing trade and refreshes the active-lessons cache the "
         "entry prompts read. Due daily from 03:00 IST, with catch-up on boot."),
        ("memory_sweep", "Pattern-memory sweep",
         "Replays real backtests after the close to refresh the case bank. "
         "Due daily from 02:00 IST."),
        ("llm_rejection_review", "LLM rejection review",
         "Scores the decisions the gates declined, after counterfactual "
         "labelling has supplied their outcomes. Due daily from 04:00 IST."),
    ):
        st = ns.get(key) or {}
        err = st.get("last_error")
        detail = st.get("detail") or "never run"
        if err:
            detail = f"{detail} — last attempt failed: {err[:120]}"
        out.append(_loop(key, label, _age_hours(st.get("last_run_at")), 48, detail, why))

    return out


async def _model_registry() -> list[dict]:
    """Registered-model state. A model that REFUSES to register is healthy —
    it means its quality gate is doing its job — so this reports facts and lets
    the UI show them without calling a refusal a fault."""
    out = []
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            for name in ("ensemble-meta-model", "confidence-calibrator",
                         "stock-predictor-xgboost", "trading-rl-policy"):
                try:
                    r = await c.get(
                        "http://mlflow:5000/api/2.0/mlflow/model-versions/search",
                        params={"filter": f"name='{name}'", "max_results": 200},
                    )
                    vs = r.json().get("model_versions", []) if r.status_code == 200 else []
                except Exception:
                    vs = []
                live = [v for v in vs if (v.get("current_stage") or "None") != "Archived"]
                out.append({
                    "name": name,
                    "versions": len(vs),
                    "active": len(live),
                    "latest": max((int(v["version"]) for v in live), default=None),
                })
    except Exception as exc:
        logger.debug("mlflow registry probe failed: %s", exc)
    return out


# ── Fault synthesis ──────────────────────────────────────────────────────────

def _faults(nodes: list[dict], edges: list[dict], loops: list[dict]) -> list[dict]:
    """Rank what is actually wrong. Severity order: down > broken loop > stale."""
    faults: list[dict] = []

    for n in nodes:
        if n.get("container") and not n.get("running"):
            faults.append({"severity": "critical", "target": n["label"],
                           "kind": "container",
                           "message": f"{n['label']} is not running ({n.get('state','unknown')})."})
        elif n.get("probe_ok") is False:
            faults.append({"severity": "critical", "target": n["label"],
                           "kind": "probe",
                           "message": f"{n['label']} failed its health probe: {n.get('probe_detail','')}."})
        elif n.get("health") == "unhealthy":
            faults.append({"severity": "critical", "target": n["label"],
                           "kind": "health",
                           "message": f"{n['label']} reports an unhealthy Docker healthcheck."})
        elif n.get("log_severity") == "error":
            faults.append({"severity": "warning", "target": n["label"],
                           "kind": "logs",
                           "message": f"{n['label']} has errors in its recent logs."})

    for e in edges:
        if e.get("status") == "down" and e.get("queue"):
            faults.append({"severity": "critical", "target": e["queue"], "kind": "queue",
                           "message": (f"Queue {e['queue']} has no consumer — "
                                       f"{e['from']}→{e['to']} messages are dropped silently.")})

    for lp in loops:
        if lp["status"] == "broken":
            age = f"{lp['age_hours']}h" if lp["age_hours"] is not None else "never"
            faults.append({"severity": "critical", "target": lp["label"], "kind": "loop",
                           "message": f"{lp['label']} has not advanced ({age}). {lp['why']}"})
        elif lp["status"] == "stale":
            faults.append({"severity": "warning", "target": lp["label"], "kind": "loop",
                           "message": f"{lp['label']} is stale ({lp['age_hours']}h since last update)."})

    order = {"critical": 0, "warning": 1}
    faults.sort(key=lambda f: order.get(f["severity"], 2))
    return faults


# ── Snapshot ─────────────────────────────────────────────────────────────────

@router.get("/snapshot")
async def snapshot(force: bool = False):
    """Full system map. Gated on an armed session so it costs nothing when the
    page is closed; `force=true` allows a one-off read (used by the landing tile
    to show a badge without arming a full session)."""
    if not force and not await _session_active():
        return {"active": False, "reason": "monitoring session not armed"}

    t0 = time.time()

    # Containers (Docker) — reuse the system panel's sweep rather than a second
    # implementation, so both surfaces always agree.
    containers: dict[str, dict] = {}
    try:
        from app.api.system import _compute_services
        snap = await _compute_services(stats=True)
        containers = {s["name"]: s for s in snap.get("data", [])}
    except Exception as exc:
        logger.warning("container sweep failed: %s", exc)

    # Probes — all concurrent, each individually bounded.
    async def _run_probe(node: dict) -> tuple[str, dict]:
        kind = node.get("probe")
        if kind == "http":
            return node["id"], await _probe_http(node["url"])
        if kind == "postgres":
            return node["id"], await _probe_postgres()
        if kind == "redis":
            return node["id"], await _probe_redis()
        if kind == "elastic":
            return node["id"], await _probe_elastic()
        if kind == "rabbitmq":
            return node["id"], await _probe_rabbitmq()
        if kind == "self":
            return node["id"], {"ok": True, "detail": "serving this request"}
        return node["id"], {}

    probe_results, loops, registry, agent_stats, ens_health = await asyncio.gather(
        asyncio.gather(*[_run_probe(n) for n in _NODES], return_exceptions=True),
        _learning_loops(),
        _model_registry(),
        _agent_stats(),
        _ensemble_engine_health(),
        return_exceptions=False,
    )
    probes = {pid: res for pid, res in
              (p for p in probe_results if isinstance(p, tuple))}
    queues = probes.get("rabbitmq", {}).get("queues", {})

    # In-process agents have no container of their own; they live and die with
    # the session runner, so their health is its health.
    runner_up = containers.get("stock-prediction-session-runner", {}).get("running")

    nodes = []
    for spec in _NODES:
        ct = containers.get(spec.get("container", ""), {})
        pr = probes.get(spec["id"], {})
        if spec.get("kind") == "inprocess":
            st = agent_stats.get(spec["agent"], {})
            nodes.append({
                "id": spec["id"], "label": spec["label"], "layer": spec["layer"],
                "kind": "inprocess", "agent": spec["agent"], "source": spec["source"],
                "container": None, "host": "stock-prediction-session-runner",
                "running": runner_up, "state": "in-process" if runner_up else "host down",
                "health": None, "log_severity": "ok",
                "probe_ok": None, "probe_detail": "runs inside the session runner",
                "weight": st.get("weight"), "buyRate": st.get("buyRate"),
                "baseRate": st.get("baseRate"), "lift": st.get("lift"),
                "predictions": st.get("predictions"),
                "github": f"{_GITHUB_BASE}/{spec['source']}",
            })
            continue
        nodes.append({
            **{k: spec[k] for k in ("id", "label", "layer")},
            "container": spec.get("container"),
            "state": ct.get("state"),
            "running": ct.get("running", False) if ct else None,
            "health": ct.get("health"),
            "status_text": ct.get("status"),
            "cpu_pct": ct.get("cpu_pct"),
            "mem_used_mb": ct.get("mem_used_mb"),
            "log_severity": ct.get("log_severity", "ok"),
            "probe_ok": pr.get("ok"),
            "probe_detail": pr.get("detail"),
        })

    by_id = {n["id"]: n for n in nodes}
    edges = []
    for e in _EDGES:
        src, dst = by_id.get(e["from"]), by_id.get(e["to"])
        status = "ok"
        detail = ""
        if not src or not dst:
            status = "unknown"
        elif src.get("running") is False or dst.get("running") is False:
            status = "down"
            detail = "endpoint container not running"
        elif src.get("probe_ok") is False or dst.get("probe_ok") is False:
            status = "degraded"
            detail = "endpoint failed its probe"
        q = e.get("queue")
        if q:
            qi = queues.get(q)
            if qi is None:
                status, detail = "unknown", "queue not found"
            elif qi["consumers"] == 0:
                status, detail = "down", "no consumer bound"
            else:
                detail = f"{qi['consumers']} consumer(s), {qi['messages']} queued"
        edges.append({**e, "status": status, "detail": detail})

    # A container running but absent from _NODES is invisible on the map — no
    # status, no probe, no log link. That is precisely the blind spot this tool
    # exists to remove, and it happened the day filebeat was added, so detect it
    # rather than rely on remembering to update the topology.
    mapped = {n["container"] for n in _NODES if n.get("container")}
    unmapped = sorted(
        name for name, ct in containers.items()
        if name.startswith("stock-prediction-")
        and name not in mapped
        # One-shot init jobs (ollama-init) exit 0 by design and are not services;
        # flagging them would make the check cry wolf on every boot.
        and ct.get("running")
    )

    faults = _faults(nodes, edges, loops)

    if ens_health and ens_health["inert"]:
        faults.append({
            "severity": "critical", "target": "Ensemble Engine", "kind": "inert-pipeline",
            "message": (
                f"Orders pipeline cannot trade: confidence peaks at "
                f"{ens_health['confCeiling']:.3f} but MIN_CONFIDENCE_TO_TRADE is "
                f"{ens_health['gate']:.2f}, so {ens_health['holdPct']:.0f}% of decisions "
                f"are HOLD and none can ever pass. HOLD votes dilute the directional "
                f"score in aggregate_signals. The 5 agent microservices feeding it "
                f"duplicate in-process backend agents that DO drive real decisions."
            ),
        })

    for name in unmapped:
        faults.append({
            "severity": "warning", "target": name, "kind": "topology",
            "message": (f"{name} is running but is not on the map — add it to "
                        f"_NODES in backend/app/api/monitor.py so it gets a "
                        f"status, probe and log link."),
        })
    return {
        "active": True,
        "generated_at": datetime.now(IST).isoformat(),
        "took_ms": int((time.time() - t0) * 1000),
        "layers": _LAYERS,
        "nodes": nodes,
        "edges": edges,
        "loops": loops,
        "registry": registry,
        "faults": faults,
        "unmapped": unmapped,
        "ensembleEngine": ens_health,
        "summary": {
            "nodes": len(nodes),
            "running": sum(1 for n in nodes if n.get("running")),
            "critical": sum(1 for f in faults if f["severity"] == "critical"),
            "warning": sum(1 for f in faults if f["severity"] == "warning"),
            "unmapped": len(unmapped),
        },
    }


# ── Kibana deep links ────────────────────────────────────────────────────────

_KIBANA_BASE = "/neuradex/dev/logs"          # SERVER_BASEPATH, proxied by nginx
_KIBANA_INTERNAL = "http://kibana:5601/neuradex/dev/logs"
_dv_cache: dict = {"id": None, "ts": 0.0}


async def _data_view_id() -> str | None:
    """Resolve the `neuradex-logs-*` data view id from Kibana (cached 10 min).

    Resolved rather than hardcoded because this instance already carries two
    data views with that same title — a hardcoded id would silently point at
    whichever one happened to be created first.
    """
    if _dv_cache["id"] and (time.time() - _dv_cache["ts"]) < 600:
        return _dv_cache["id"]
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.get(f"{_KIBANA_INTERNAL}/api/data_views",
                            headers={"kbn-xsrf": "true"})
            r.raise_for_status()
            views = r.json().get("data_view", [])
        # Prefer the COMBINED view (app logs + container logs). Only 14 of 29
        # containers use the Python elk_logger; the rest — postgres, redis,
        # nginx, the Java services — reach Elasticsearch solely via Filebeat's
        # `neuradex-docker-*` index. A link bound to `neuradex-logs-*` alone
        # opens an empty Discover for half the nodes on the map.
        views.sort(key=lambda v: "neuradex-docker" not in v.get("title", ""))
        match = next((v for v in views if v.get("title", "").startswith("neuradex-")), None)
        if match:
            _dv_cache.update({"id": match["id"], "ts": time.time()})
            return match["id"]
    except Exception as exc:
        logger.debug("kibana data view lookup failed: %s", exc)
    return None


def _discover_url(dv: str | None, kuery: str, columns: list[str], minutes: int = 60) -> str:
    """A Kibana Discover URL with the query already applied.

    Kibana encodes `_a`/`_g` as rison in the URL fragment. Single quotes inside
    a rison string are escaped by doubling them.
    """
    from urllib.parse import quote
    q = kuery.replace("'", "''")
    cols = ",".join(columns)
    g = f"(time:(from:now-{minutes}m,to:now))"
    a = (f"(columns:!({cols}),"
         f"index:'{dv}'," if dv else f"(columns:!({cols}),")
    a += (f"query:(language:kuery,query:'{q}'),"
          f"sort:!(!('@timestamp',desc)))")
    return f"{_KIBANA_BASE}/app/discover#/?_g={quote(g, safe='(),:-')}&_a={quote(a, safe='(),:-!*')}"


def _kibana_links(service: str, dv: str | None) -> dict:
    """Per-component log views. `service` matches the elk_logger `service` field."""
    base = f'service:"{service}"'
    return {
        "all": _discover_url(dv, base, ["level", "logger", "message"]),
        "errors": _discover_url(dv, f'{base} and level:"ERROR"', ["logger", "message", "exception"], 240),
        # request→response pairs. RequestLoggingMiddleware also captures
        # response_body, which is the whole point of "see the request and its
        # response" — sorting by request_id groups each pair together.
        "requests": _discover_url(
            dv,
            f'{base} and log_type:("api_request" or "api_response" or "api_error")',
            ["log_type", "http_method", "path", "status_code", "duration_ms",
             "request_id", "response_body"],
        ),
        "slow": _discover_url(dv, f'{base} and duration_ms > 1000',
                              ["http_method", "path", "status_code", "duration_ms"], 240),
    }


@router.get("/component/{node_id}")
async def component_detail(node_id: str):
    """Code-level detail for one component: what it does, its entry point, the
    method-level flow, its queues/tables, real gotchas, and Kibana deep links."""
    from app.api.component_docs import for_component, for_vote_agent
    spec = next((n for n in _NODES if n["id"] == node_id), None)
    dv = await _data_view_id()

    # In-process agents are not containers, so they get their own detail:
    # source file, live weight, measured accuracy, and the ensemble's own logs.
    if spec and spec.get("kind") == "inprocess":
        stats = (await _agent_stats()).get(spec["agent"], {})
        return {
            "id": node_id, "label": spec["label"], "container": None,
            "kind": "inprocess", "agent": spec["agent"],
            "docs": for_vote_agent(spec["agent"], spec["label"], spec["source"]),
            "stats": stats,
            # Its log lines carry the runner's service name, filtered to the
            # ensemble logger so you see the votes rather than the whole runner.
            "kibana": _kibana_links("session-runner", dv),
            "kibanaReady": dv is not None,
        }

    docs = for_component(node_id)

    # elk_logger stamps `service` from SERVICE_NAME, which is the compose
    # service name — the container name minus the project prefix.
    service = (spec or {}).get("container", "").replace("stock-prediction-", "") or node_id
    return {
        "id": node_id,
        "label": (spec or {}).get("label", node_id),
        "container": (spec or {}).get("container"),
        "docs": docs,
        "kibana": _kibana_links(service, dv),
        "kibanaReady": dv is not None,
    }


# ── Page → request → handler tracing ─────────────────────────────────────────

@router.get("/pages")
async def list_pages(refresh: bool = False):
    """Every frontend page/component that calls the API, with how many requests
    it makes and which of them fire on page load."""
    from app.api import request_flow
    if refresh:
        request_flow.refresh()
    pages = request_flow.list_pages()
    routes = request_flow.scan_routes()
    return {
        "pages": pages,
        "totals": {
            "pages": sum(1 for p in pages if p["kind"] == "page"),
            "components": sum(1 for p in pages if p["kind"] != "page"),
            "apiMethods": len(request_flow.scan_api_client()),
            "backendRoutes": len(routes),
        },
    }


@router.get("/page/{name}")
async def page_detail(name: str):
    """One page: every API call it makes, what triggers it, and the end-to-end
    chain from the call site through nginx to the backend handler."""
    from app.api import request_flow
    dv = await _data_view_id()
    detail = request_flow.page_detail(name, dv)
    if not detail:
        return {"error": f"no API calls found for '{name}'"}
    return detail


@router.get("/trace")
async def trace_endpoint(method: str):
    """End-to-end chain for a single apiService method."""
    from app.api import request_flow
    dv = await _data_view_id()
    return request_flow.trace_call(method, dv)


# ── Request/response exchanges, served inline ────────────────────────────────
# Kibana is great for exploration but a poor dependency for "show me what this
# endpoint did": it needs its own JS app to boot, several XHRs, and a data view
# to resolve — which is exactly what fails on a phone over a tunnel. The data
# lives in Elasticsearch, so read it directly and render it in the page.

_ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")


@router.get("/requests")
async def recent_requests(path: str, limit: int = 25, hours: int = 96):
    """Recent request→response pairs for one API path.

    The `path` recorded by RequestLoggingMiddleware is the FULL proxied path
    (`/neuradex/backend/api/agent/analyze/BPCL`), while callers ask about the
    API path (`/api/agent/analyze`). `path` is a text field, so a match_phrase
    matches the token run regardless of the proxy prefix or a trailing id.
    """
    query = {
        "size": max(limit * 3, 60),
        "sort": [{"@timestamp": "desc"}],
        "query": {
            "bool": {
                "must": [
                    {"match_phrase": {"path": path.rstrip("*").rstrip("/")}},
                    {"range": {"@timestamp": {"gte": f"now-{hours}h"}}},
                ],
                "should": [
                    {"match_phrase": {"log_type": "api_request"}},
                    {"match_phrase": {"log_type": "api_response"}},
                    {"match_phrase": {"log_type": "api_error"}},
                ],
                "minimum_should_match": 1,
            }
        },
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(f"{_ES_URL}/neuradex-logs-*/_search", json=query)
            r.raise_for_status()
            hits = r.json().get("hits", {}).get("hits", [])
    except Exception as exc:
        logger.warning("request log query failed: %s", exc)
        return {"path": path, "exchanges": [], "error": str(exc)[:160]}

    # Pair request and response by request_id. Docs without one still surface as
    # their own single-sided entry rather than being dropped silently.
    pairs: dict[str, dict] = {}
    loose: list[dict] = []
    for h in hits:
        s = h.get("_source", {})
        rid = s.get("request_id")
        kind = str(s.get("log_type", ""))
        rec = {
            "ts": s.get("@timestamp"), "method": s.get("http_method"),
            "path": s.get("path"), "status": s.get("status_code"),
            "durationMs": s.get("duration_ms"), "clientIp": s.get("client_ip"),
            "responseBody": s.get("response_body"), "message": s.get("message"),
            "level": s.get("level"),
        }
        if not rid:
            loose.append({"requestId": None, "kind": kind, **rec})
            continue
        e = pairs.setdefault(rid, {"requestId": rid, "ts": rec["ts"], "method": None,
                                   "path": None, "status": None, "durationMs": None,
                                   "clientIp": None, "responseBody": None,
                                   "error": None, "hasRequest": False, "hasResponse": False})
        if kind == "api_request":
            e["hasRequest"] = True
            e["method"] = e["method"] or rec["method"]
            e["path"] = e["path"] or rec["path"]
            e["clientIp"] = e["clientIp"] or rec["clientIp"]
            e["ts"] = rec["ts"] or e["ts"]
        elif kind == "api_response":
            e["hasResponse"] = True
            e["status"] = rec["status"]
            e["durationMs"] = rec["durationMs"]
            e["responseBody"] = rec["responseBody"]
            e["method"] = e["method"] or rec["method"]
            e["path"] = e["path"] or rec["path"]
        else:
            e["error"] = rec["message"]
            e["status"] = e["status"] or rec["status"]

    exchanges = sorted(pairs.values(), key=lambda e: e["ts"] or "", reverse=True)[:limit]
    return {
        "path": path,
        "exchanges": exchanges,
        "unpaired": loose[:5],
        "count": len(exchanges),
        "windowHours": hours,
    }


# ── Log store: size, retention window, manual purge ──────────────────────────

@router.get("/logs-store")
async def logs_store():
    """Index sizes, the retention window, and how much is due to age out."""
    from app.data.log_retention import stats
    return await stats()


@router.post("/logs-store/retention")
async def set_log_retention(days: int):
    """Change the retention window. Stored in Redis so it takes effect without
    a restart; the nightly prune reads it each run."""
    from app.data.log_retention import set_retention_days, stats
    applied = await set_retention_days(days)
    return {"retentionDays": applied, **(await stats())}


@router.post("/logs-store/prune")
async def prune_logs(days: int | None = None, dry_run: bool = False):
    """Drop dated indices older than the window. `dry_run` lists what would go."""
    from app.data.log_retention import prune
    return await prune(days=days, dry_run=dry_run)


@router.post("/logs-store/purge")
async def purge_logs(service: str | None = None, keep_today: bool = True):
    """Clear logs — one service's documents, or every dated index.

    Destructive and irreversible, so it is never wired to a bare click in the
    UI: the caller must pass an explicit target.
    """
    from app.data.log_retention import purge_all, purge_service
    if service:
        return await purge_service(service)
    return await purge_all(keep_today=keep_today)


# ── Decision publishing (backend ensemble -> execution chain) ────────────────

@router.get("/publish-flag")
async def get_publish_flag():
    """Whether the backend publishes decisions into the execution chain."""
    from app.utils.decision_publisher import is_enabled, env_default
    return {"enabled": await is_enabled(), "envDefault": env_default(),
            "chain": "backend ensemble -> ensemble.raw -> ensemble-engine -> "
                     "ensemble.decision -> risk-engine -> trade-executor"}


@router.post("/publish-flag")
async def set_publish_flag(enabled: bool):
    """Arm or disarm publishing at runtime (no redeploy).

    Live-behaviour switch: with this on, the backend ensemble's decisions reach
    risk-engine and, if they clear its gate, trade-executor. That executor runs
    PAPER_TRADING_MODE=true with no Groww keys, so orders are paper.
    """
    from app.utils.decision_publisher import set_enabled, is_enabled
    await set_enabled(enabled)
    return {"enabled": await is_enabled()}


@router.get("/logs/{name}")
async def component_logs(name: str, tail: int = 200):
    """Recent logs for one container, as plain lines for the detail drawer."""
    from app.api.system import _PROJECT_PREFIX, _client, _demux
    if not name.startswith(_PROJECT_PREFIX):
        return {"error": "Only project containers can be inspected."}
    try:
        async with _client() as c:
            r = await c.get(
                f"/containers/{name}/logs",
                params={"stdout": 1, "stderr": 1, "tail": tail, "timestamps": 0},
            )
            r.raise_for_status()
            text = _demux(r.content)
        return {"name": name, "lines": text.splitlines()[-tail:]}
    except Exception as exc:
        return {"name": name, "lines": [], "error": str(exc)[:200]}


@router.get("/evidence")
async def get_evidence_register():
    """What justifies each live trading behaviour.

    The validation gate is advisory on its own. This is the register that makes
    it binding: every switch able to change what the system trades declares the
    evidence behind its current setting, and `test_registry.py` fails the build
    when live code reads one that is not declared here.

    Enforcement is at build time rather than startup on purpose — refusing to
    boot over a bookkeeping gap would be a worse failure than the one it
    prevents — so this endpoint exists to make an unjustified behaviour visible.
    """
    from app.research.registry import evidence_debt
    return {"status": "success", "data": evidence_debt()}
