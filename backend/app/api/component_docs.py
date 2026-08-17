"""Component knowledge base — the code-level layer of the System Map.

Answers, for any component: what does it do, where does its code live, what runs
when a request/message arrives, and where do I look when it breaks.

Two deliberate design choices:

1. **Symbols, not line numbers.** Every reference stores a file plus a SYMBOL
   name (`def foo` / `class Foo` / a literal string). The line is resolved
   against the working tree at request time by `_resolve`. Hardcoded line
   numbers rot the moment anyone edits a file above them, and a monitor that
   confidently points at the wrong line is worse than one that points at none.
   If a symbol can no longer be found the reference degrades to a file-level
   link and is flagged `stale: true` — which is itself a useful signal that the
   docs have drifted from the code.

2. **Gotchas are real incidents.** Each `gotchas` entry is something that has
   actually broken here, not generic advice. That is the part a newcomer cannot
   get from reading the source.

The repo is mounted read-only at /repo (see docker-compose.yml).
"""
from __future__ import annotations

import functools
import os
import re

REPO_ROOT = os.environ.get("REPO_ROOT", "/repo")
GITHUB_BASE = "https://github.com/AbhinavShah421/NeuradeX/blob/main"


def _find_symbol(path: str, symbol: str) -> int | None:
    """Line number of `symbol` in `path`, or None. Tries a def/class definition
    first, then a bare literal (for queue names, route paths, config keys)."""
    full = os.path.join(REPO_ROOT, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return None

    patterns = [
        re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(symbol)}\s*\("),
        re.compile(rf"^\s*class\s+{re.escape(symbol)}\b"),
        re.compile(rf"^\s*{re.escape(symbol)}\s*[:=]"),
        re.compile(re.escape(symbol)),
    ]
    for pat in patterns:
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                return i
    return None


@functools.lru_cache(maxsize=512)
def _resolve(path: str, symbol: str | None) -> dict:
    line = _find_symbol(path, symbol) if symbol else None
    exists = os.path.exists(os.path.join(REPO_ROOT, path))
    url = f"{GITHUB_BASE}/{path}" + (f"#L{line}" if line else "")
    return {
        "path": path, "symbol": symbol, "line": line,
        "exists": exists, "github": url,
        "stale": bool(symbol) and line is None and exists,
        "label": f"{path}:{line}" if line else path,
    }


def _ref(path: str, symbol: str | None = None) -> dict:
    return _resolve(path, symbol)


# ── The knowledge base ───────────────────────────────────────────────────────
# `flow` is the method-level narrative: what actually executes, in order.
_DOCS: dict[str, dict] = {
    "backend": {
        "role": "FastAPI application — every HTTP route the UI calls, plus the AI engine.",
        "language": "Python · FastAPI",
        "entry": ("backend/app/main.py", "lifespan"),
        "flow": [
            ("Request arrives", "backend/app/middleware/logging_middleware.py", "RequestLoggingMiddleware",
             "Stamps a request_id, logs log_type=api_request, and on the way out logs api_response with status_code and duration_ms. This is what the Kibana request/response links filter on."),
            ("Routed to an API module", "backend/app/main.py", "include_router",
             "Each feature area is its own router under /api/<area>."),
            ("Handler calls a service", "backend/app/services/ai_engine_service.py", "analyze",
             "Routers stay thin; the work lives in app/services/*."),
            ("Ensemble votes", "backend/app/agents/ensemble.py", "decide",
             "Collects every agent signal, applies learned weights, and produces BUY/SELL/HOLD with a confidence."),
            ("Outcome recorded", "backend/app/agents/learning.py", "record_outcome",
             "On a closed trade: updates per-agent weights, the RL Q-table and the pattern-memory bank."),
        ],
        "endpoints": [
            ("GET", "/api/ai-engine/performance", "backend/app/api/ai_engine.py", "get_performance"),
            ("POST", "/api/ai-engine/pattern-model/train", "backend/app/api/ai_engine.py", "pattern_model_train"),
            ("GET", "/api/monitor/snapshot", "backend/app/api/monitor.py", "snapshot"),
            ("GET", "/api/system/services", "backend/app/api/system.py", "list_services"),
        ],
        "tables": ["session_decisions", "ai_engine_predictions", "ai_engine_outcomes",
                   "ai_engine_agent_weights", "pattern_memory", "trade_records"],
        "gotchas": [
            "backend/app is BIND-MOUNTED — deploy is `docker restart`, not a rebuild.",
            "Runs with BACKEND_ROLE=api: scheduled loops (pattern/GBM retrain, counterfactual) run in session-runner, NOT here.",
        ],
    },
    "runner": {
        "role": "Same image as the backend, BACKEND_ROLE=runner — owns every scheduled/background loop.",
        "language": "Python · asyncio",
        "entry": ("backend/app/main.py", "lifespan"),
        "flow": [
            ("Role gate on startup", "backend/app/main.py", "_role",
             "Only the 'full' and 'runner' roles start the compute loops, so the API container stays responsive."),
            ("Live session loop", "backend/app/api/sessions.py", "session_runner_loop",
             "Advances each live paper-trading session bar by bar and writes to session_decisions."),
            ("Pattern model nightly", "backend/app/agents/pattern_model.py", "pattern_autotrain_loop",
             "01:00 IST retrain. Added 2026-08-17 — previously only the backtest autopilot triggered it, so it froze for five weeks."),
            ("GBM nightly", "backend/app/agents/pattern_model.py", "gbm_autotrain_loop", "03:00 IST, daily + intraday slots."),
            ("Counterfactual labelling", "backend/app/agents/counterfactual.py", "counterfactual_loop",
             "Off-hours: labels the decisions the gates DECLINED against recorded ticks, so learning isn't limited to the few trades taken."),
        ],
        "tables": ["session_decisions", "session_metadata", "pattern_model_state", "gbm_model_state"],
        "gotchas": [
            "If a loop stops, nothing throws — the System Map grades these by staleness for exactly that reason.",
            "Single writer for the 1s tick store; never run a second runner.",
        ],
    },
    "ensemble": {
        "role": "Microservice that aggregates agent signals into one decision (Orders pipeline).",
        "language": "Python · FastAPI + aio_pika",
        "entry": ("ensemble-engine/app/main.py", "lifespan"),
        "flow": [
            ("Collect agent signals", "ensemble-engine/app/agent_collector.py", "AgentSignalCollector",
             "Waits up to AGENT_SIGNAL_TIMEOUT_SECONDS for each agent to answer on agent.signals."),
            ("Load learned weights", "ensemble-engine/app/main.py", "_load_weights_from_db",
             "Reads the agent_weights table; falls back to DEFAULT_WEIGHTS if empty."),
            ("Weighted vote", "ensemble-engine/app/aggregator.py", "aggregate_signals",
             "Weights each agent's confidence and produces weighted_confidence + agreement_score."),
            ("Meta-model gate", "ensemble-engine/app/meta_model.py", "predict_win_probability",
             "Optional second gate: downgrades to HOLD when predicted win probability < META_WIN_PROB_GATE."),
            ("Confidence gate then calibration", "ensemble-engine/app/main.py", "on_all_signals_received",
             "IMPORTANT ORDER: the MIN_CONFIDENCE_TO_TRADE gate runs on the RAW score; calibration happens AFTER, so it only changes the reported number, never the decision."),
        ],
        "consumes": ["agent.signals"],
        "publishes": ["ensemble.decision"],
        "tables": ["agent_weights"],
        "gotchas": [
            "Two separate ensembles exist: this microservice (Orders page) and backend/app/agents/ensemble.py (AI Engine page). Confirm which one a change affects.",
            "The model loaders fall back to the highest version number IGNORING stage — archiving a bad model does nothing unless the loader filters Archived.",
        ],
    },
    "feedback": {
        "role": "Stores every closed trade and advances the legacy ensemble weights.",
        "language": "Python · FastAPI + aio_pika",
        "entry": ("feedback-service/app/main.py", "lifespan"),
        "flow": [
            ("Ingest — HTTP path", "feedback-service/app/main.py", "post_trades",
             "POST /trades. This is how essentially EVERY trade arrives (backtests and paper sessions post here directly)."),
            ("Ingest — queue path", "feedback-service/app/main.py", "_consumer_loop",
             "Consumes trade.outcomes.feedback. Only the live trade-executor publishes here."),
            ("Store the row", "feedback-service/app/main.py", "_store_trade_record", "Upsert into trade_records on trade_id."),
            ("Advance the loop", "feedback-service/app/main.py", "_apply_trade_outcome",
             "Shared by BOTH ingest paths. Gated to PAPER/LIVE so replay and backtest cannot contaminate live weights."),
            ("Score each agent", "feedback-service/app/weight_updater.py", "compute_weight_updates",
             "Agents that voted with the outcome gain weight; absent agents are left untouched rather than penalised."),
        ],
        "consumes": ["trade.outcomes.feedback"],
        "tables": ["trade_records", "agent_weights"],
        "gotchas": [
            "POST /trades bypasses RabbitMQ. It used to only store the row, so agent_weights sat frozen from 2026-05-30 while trade_records grew to 14k rows. Fixed 2026-08-17.",
            "agent_signals is stored FLAT ({\"technical\": \"BUY\"}), not nested. Code expecting {\"signal\": ...} raises AttributeError on every message.",
        ],
    },
    "rl": {
        "role": "Reinforcement-learning signal agent.",
        "language": "Python · FastAPI",
        "entry": ("rl-agent/app/main.py", "_consumer_loop"),
        "flow": [
            ("Consume market data", "rl-agent/app/main.py", "_consumer_loop", "Subscribes to market.data.rl only."),
            ("Load the policy", "rl-agent/app/policy.py", "get_policy",
             "Tries MLflow Production then Staging. No policy registered → returns None."),
            ("Predict", "rl-agent/app/policy.py", "predict_action",
             "With a policy: model inference. WITHOUT one: falls through to a hardcoded RSI/MACD heuristic — which is what is running today."),
        ],
        "consumes": ["market.data.rl"],
        "gotchas": [
            "/health reports policy_loaded:false — model-trainer correctly REFUSES to register (Sharpe 0.655 < 1.0), so the heuristic fallback serves instead. The gate is right; the model has no edge.",
            "RL learning from outcomes happens in the BACKEND Q-table (learning.py record_outcome), not here. The trade.outcomes.rl queue was removed 2026-08-17 — declared and bound, but no consumer was ever written.",
        ],
    },
    "trainer": {
        "role": "Scheduled model training; registers to MLflow only when a quality gate passes.",
        "language": "Python · LightGBM / XGBoost / PPO",
        "entry": ("model-trainer/app/main.py", "_scheduled_retrain_loop"),
        "flow": [
            ("Run all trainers", "model-trainer/app/main.py", "_run_all_trainers", "XGBoost, RL, meta-model, calibrator."),
            ("Meta-model", "model-trainer/app/trainers/meta_trainer.py", "train_meta_model",
             "Learns WHEN the ensemble vote is trustworthy. Gated on AUC (>= MIN_AUC), not accuracy."),
            ("Feature build", "model-trainer/app/trainers/meta_trainer.py", "_extract_features",
             "Must mirror ensemble-engine/app/meta_model.py _build_meta_features EXACTLY — 30 features, same order."),
            ("Calibrator", "model-trainer/app/trainers/calibration_trainer.py", "train_calibrator",
             "Platt scaling on a chronological holdout, compared against a constant-baseline Brier."),
        ],
        "gotchas": [
            "Accuracy is a useless gate on a 33/67 imbalanced label — always predicting LOSS scores 0.669. Both gates now use AUC / out-of-sample Brier.",
            "Data is loaded newest-first; the split takes X[:80%] as TRAIN. Ordering must be ASC or you train on the future and test on the past (this inflated meta-model AUC from 0.48 to 0.75).",
            "No bind mount — code changes need `compose build` + `up -d`, not a restart.",
        ],
    },
    "scanner": {
        "role": "Sweeps the NSE universe and ranks candidates.",
        "language": "Python · FastAPI",
        "entry": ("stock-scanner/app/main.py", None),
        "tables": ["scan_evaluations"],
        "gotchas": ["No bind mount — a restart silently no-ops a code change; rebuild the image."],
    },
    "executor": {
        "role": "Places orders (paper by default) and publishes the outcome.",
        "language": "Java · Spring Boot",
        "entry": ("trade-executor/src/main/java/com/neuradex/trade/consumer/RiskValidatedConsumer.java", "RiskValidatedConsumer"),
        "flow": [
            ("Consume validated intent", "trade-executor/src/main/java/com/neuradex/trade/consumer/RiskValidatedConsumer.java", "risk.validated", "Only risk-approved orders reach here."),
            ("Publish the outcome", "trade-executor/src/main/java/com/neuradex/trade/consumer/RiskValidatedConsumer.java", "trade.outcomes", "Fanout to the feedback service."),
        ],
        "consumes": ["risk.validated"],
        "publishes": ["trade.outcomes"],
        "gotchas": ["PAPER_TRADING_MODE=true by default. This path only runs for LIVE orders — paper sessions POST straight to feedback-service instead."],
    },
    "market": {
        "role": "Fetches candles/ticks and fans them out to every agent.",
        "language": "Python · FastAPI",
        "entry": ("market-data-service/app/main.py", None),
        "flow": [
            ("Declare topology", "market-data-service/app/services/rabbitmq_setup.py", "setup_topology",
             "Single source of truth for every exchange, queue and binding in the system."),
        ],
        "publishes": ["market.data.technical", "market.data.sentiment", "market.data.macro",
                      "market.data.pattern", "market.data.rl"],
        "gotchas": ["Adding a queue here without writing its consumer creates a silent black hole — it accepts publishes and discards them."],
    },
    "risk": {
        "role": "Position sizing and risk vetoes before anything is executed.",
        "language": "Java · Spring Boot",
        "entry": ("risk-engine/src/main/java/com/neuradex/risk/RiskEngineApplication.java", "RiskEngineApplication"),
        "flow": [
            ("Consume the decision", "risk-engine/src/main/java/com/neuradex/risk/consumer/EnsembleConsumer.java",
             "EnsembleConsumer", "Reads ensemble.decision — every candidate trade the ensemble produced."),
            ("Apply risk rules", "risk-engine/src/main/java/com/neuradex/risk/service/RiskValidatorService.java",
             "RiskValidatorService", "Position sizing and veto rules; only survivors are forwarded."),
            ("Publish validated intent", "risk-engine/src/main/java/com/neuradex/risk/config/RabbitConfig.java",
             "RabbitConfig", "Declares the risk.validated queue the trade-executor consumes."),
        ],
        "consumes": ["ensemble.decision"],
        "publishes": ["risk.validated"],
    },
    "technical":  {"role": "RSI / MACD / VWAP / moving-average structure.", "language": "Python", "entry": ("technical-agent/app/main.py", None), "consumes": ["market.data.technical"], "publishes": ["agent.signals"]},
    "sentiment":  {"role": "News and sentiment tilt.", "language": "Python", "entry": ("sentiment-agent/app/main.py", None), "consumes": ["market.data.sentiment"], "publishes": ["agent.signals"],
                   "gotchas": ["Excluded from replay/backtest — dated news search is unreliable and made replays non-reproducible."]},
    "macro":      {"role": "Index/regime context (NIFTY, VIX).", "language": "Python", "entry": ("macro-agent/app/main.py", None), "consumes": ["market.data.macro"], "publishes": ["agent.signals"]},
    "pattern":    {"role": "Candlestick pattern recognition.", "language": "Python", "entry": ("pattern-agent/app/main.py", None), "consumes": ["market.data.pattern"], "publishes": ["agent.signals"]},
    "autopilot":  {"role": "Drives unattended paper-trading and backtest sweeps.", "language": "Python",
                   "entry": ("autopilot-service/app/autopilot.py", "_do_backtest_step"),
                   "gotchas": ["Its enable flag is a Redis key with a TTL — it can disarm itself silently.",
                               "It used to be the ONLY trigger for pattern-model training; disabling it froze that model for five weeks."]},
    "groww":      {"role": "Broker websocket feed (live ticks).", "language": "Python", "entry": ("groww-feed-service/app/main.py", None),
                   "gotchas": ["Tokens expire 06:00 IST while containers run UTC — check the clock before debugging a dead feed."]},
    "sentsvc":    {"role": "Standalone sentiment scoring service.", "language": "Python", "entry": ("sentiment-service/app/main.py", None)},
    "frontend":   {"role": "React + Vite SPA served as a static build.", "language": "TypeScript · React",
                   "entry": ("frontend/src/App.tsx", None),
                   "flow": [("Routes", "frontend/src/App.tsx", "Routes", "React Router with basename=/neuradex."),
                            ("API client", "frontend/src/services/api.ts", "ApiService", "Single axios instance for every call."),
                            ("System Map", "frontend/src/pages/SystemMap.tsx", "SystemMap", "This page.")],
                   "gotchas": ["Static build — no hot reload. `compose build frontend && up -d`, then restart nginx.",
                               "The axios interceptor camelCases EVERY response key: backend probe_ok arrives as probeOk."]},
    "nginx":      {"role": "Single entry point; proxies the app, API, docs, Kibana and Adminer.", "language": "nginx",
                   "entry": ("config/nginx.conf", None),
                   "gotchas": ["Recreating an upstream container can 502 the whole app — restart nginx after `up -d`."]},
    "postgres":   {"role": "System of record: trades, decisions, weights, memory.", "language": "PostgreSQL 15",
                   "entry": ("scripts/init_timescale.sql", None),
                   "tables": ["trade_records", "session_decisions", "ai_engine_agent_weights", "agent_weights", "pattern_memory"]},
    "redis":      {"role": "Cache, feature flags, live weights, monitor session key.", "language": "Redis"},
    "rabbitmq":   {"role": "Message bus for the agent→ensemble→risk→executor pipeline.", "language": "RabbitMQ",
                   "entry": ("market-data-service/app/services/rabbitmq_setup.py", "QUEUE_BINDINGS"),
                   "gotchas": ["A queue with ZERO consumers accepts publishes and drops them silently — the System Map grades those as down."]},
    "elastic":    {"role": "Log store behind Kibana.", "language": "Elasticsearch 8",
                   "entry": ("shared/python/elk_logger.py", "_build_doc"),
                   "gotchas": ["Dynamic mapping locks a field's type from its FIRST value — one numeric `args` made every later string-args log line fail to index and vanish."]},
    "kibana":     {"role": "Log search UI. The System Map deep-links into it per component.", "language": "Kibana 8"},
    "mlflow":     {"role": "Model registry and experiment tracking.", "language": "MLflow"},
    "influxdb":   {"role": "Time-series store for market data.", "language": "InfluxDB"},
    "mongodb":    {"role": "Document store (news, unstructured).", "language": "MongoDB"},
    "ollama":     {"role": "Local LLM for trade review and post-mortems.", "language": "Ollama · llama3.1:8b"},
    "ngrok":      {"role": "Public tunnel to nginx.", "language": "ngrok"},
}


# Per-agent notes. Only where there is something true and non-obvious to say —
# an empty entry is better than filler.
_AGENT_NOTES: dict[str, list[str]] = {
    "rl": ["Q-table updated by learning.py record_outcome, NOT by the rl-agent microservice.",
           "The Q-update reuses the entry state as next_state, so it is reward-only with no bootstrapping."],
    "memory": ["Runs the evidence gate: vetoes a directional call when similar past setups did not pay.",
               "Needs >= 8 cases of that SPECIFIC action before it can gate; below that it stays out of the way."],
    "anomaly": ["Veto-only — never takes a direction, so it is excluded from weight learning entirely.",
                "It once voted HOLD on 200,877 of 200,877 bars and collected weight for abstaining; that is why it is pinned."],
    "gbm": ["Nightly retrain at 03:00 IST; daily + intraday slots. Registered only if it beats its gate."],
    "sentiment": ["Excluded from replay/backtest — dated news search is unreliable and made replays non-reproducible."],
    "day_structure": ["Intraday S/R, day-range position and R/R — exists to stop the ensemble buying into day highs."],
    "regime": ["Classifies trend/chop/high-vol and reweights momentum against mean-reversion."],
}


def for_vote_agent(agent: str, label: str, source: str) -> dict:
    """Docs for an in-process ensemble agent (no container of its own)."""
    return {
        "role": f"{label} — one of the 12 agents the backend ensemble votes with. "
                f"Runs in-process inside the session runner, not as a container.",
        "language": "Python (in-process)",
        "entry": _ref(source, None),
        "flow": [
            {"step": "Invoked per bar", "why": "The session runner calls every agent on each candle.",
             "ref": _ref("backend/app/api/sessions.py", "session_runner_loop")},
            {"step": "Produces a signal", "why": f"{label} returns BUY/SELL/HOLD with a confidence.",
             "ref": _ref(source, None)},
            {"step": "Weighted into the vote", "why": "The ensemble scales each vote by its learned weight and action-rate lift.",
             "ref": _ref("backend/app/agents/ensemble.py", "decide")},
            {"step": "Weight updated on outcome", "why": "record_outcome credits or debits the agent by whether its own call was right.",
             "ref": _ref("backend/app/agents/learning.py", "record_outcome")},
        ],
        "endpoints": [],
        "consumes": [], "publishes": [],
        "tables": ["ai_engine_agent_weights", "session_decisions"],
        "gotchas": _AGENT_NOTES.get(agent, []) + [
            "No container: if the session runner is down, this agent is not running either.",
        ],
    }


def for_component(node_id: str) -> dict | None:
    """Full documentation for one node, with every reference resolved to a
    current line number."""
    d = _DOCS.get(node_id)
    if not d:
        return None

    out: dict = {
        "role": d.get("role"),
        "language": d.get("language"),
        "consumes": d.get("consumes", []),
        "publishes": d.get("publishes", []),
        "tables": d.get("tables", []),
        "gotchas": d.get("gotchas", []),
    }
    if d.get("entry"):
        out["entry"] = _ref(*d["entry"])
    out["flow"] = [
        {"step": step, "why": why, "ref": _ref(path, sym)}
        for (step, path, sym, why) in d.get("flow", [])
    ]
    out["endpoints"] = [
        {"method": m, "path": p, "ref": _ref(fp, sym)}
        for (m, p, fp, sym) in d.get("endpoints", [])
    ]
    return out
