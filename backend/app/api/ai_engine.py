"""AI Engine REST API — analyze, record outcomes, performance, history.

This is a thin FastAPI router: it parses/validates requests and delegates all
business logic (DB/data-provider access, calculations, microservice proxying)
to app.services.ai_engine_service. See that module's docstring for the list
of names re-exported below for other modules that import internals from here.
"""
from __future__ import annotations
from fastapi import APIRouter
from typing import Optional

from app.utils.elk_logger import get_logger
from app.services import ai_engine_service as service

# Pydantic request models — defined in the service module, imported here for
# use as route parameter types. Schema is unchanged, only the module moved.
from app.services.ai_engine_service import (
    AnalyzeRequest,
    OutcomeRequest,
    ScanFeedback,
    AutopilotRequest,
    TradeGateRequest,
    WatchlistConfigRequest,
    BatchSizeRequest,
    SpeedRequest,
    PaperTimingRequest,
    LearningEventRequest,
    SeedMemoryRequest,
    PatternTrainRequest,
    ModelConfigRequest,
)

# Re-exported for external consumers that import these names directly from
# app.api.ai_engine — do not remove without updating those call sites:
#   app.api.sessions: _log_system_event
#   app.api.delivery_paper: _ensure_scan_eval
from app.services.ai_engine_service import (  # noqa: F401
    _log_system_event,
    _ensure_scan_eval,
)

router = APIRouter()
logger = get_logger(__name__)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run all 6 agents and return the ensemble decision."""
    return await service.analyze(req)


@router.post("/outcome")
async def record_outcome(req: OutcomeRequest):
    """Record trade outcome — record_outcome updates agent weights + RL Q-table + memory."""
    return await service.record_outcome(req)


@router.get("/performance")
async def get_performance():
    """Per-agent weight and accuracy stats."""
    return await service.get_performance()


@router.get("/agent-action-trend")
async def get_agent_action_trend(agent: str, action: str):
    """Per-day accuracy trend for a specific agent + action (BUY/SELL/HOLD).

    Returns a list of {date, total, correct, accuracy} sorted by date so the
    frontend can plot a line chart showing how this agent's vote quality has
    changed over time.
    """
    return await service.get_agent_action_trend(agent, action)


@router.get("/history")
async def get_history(symbol: Optional[str] = None, limit: int = 20):
    """Recent predictions with outcomes."""
    return await service.get_history(symbol, limit)


@router.get("/weights")
async def get_weights():
    """Current agent weights."""
    return await service.get_weights()


@router.get("/learning-summary")
async def learning_summary():
    """Aggregate training status — proves every backtest/paper trade trains the agents."""
    return await service.learning_summary()


# ── AI Watchlist (self-running market scanner) ────────────────────────────────

@router.get("/watchlist")
async def get_watchlist(min_grade: str | None = None):
    """The live AI watchlist produced by the stock-scanner service (read from
    Redis), each item enriched with its latest LLM news-sentiment signal
    (produced by the sentiment-service).

    min_grade: optional A/B/C filter — keep only items at or above that grade
    (A is best). Use it to surface only high win-probability setups."""
    return await service.get_watchlist(min_grade)


@router.get("/ranked")
async def get_ranked(limit: int = 100):
    """The full ranked board of AI-scanned stocks (for the Predictions page),
    each enriched with its latest LLM news-sentiment. `limit` caps how many of the
    top-ranked names to return."""
    return await service.get_ranked(limit)


@router.get("/scan-diff")
async def scan_diff(limit: int = 60):
    """How this scan's ranking differs from the previous completed scan: per-stock
    rank moves (with the reason), names that entered the board, and names that
    dropped out. Powers the AI Watchlist 'what changed' view."""
    return await service.scan_diff(limit)


@router.post("/watchlist/scan")
async def scan_watchlist():
    """Ask the stock-scanner microservice to run an immediate full market sweep."""
    return await service.scan_watchlist()


@router.post("/backfill-delivery")
async def backfill_delivery(days: int = 14, limit: int = 250):
    """Ask the scanner to reconstruct delivery-pick accuracy history so the AI Scan
    Accuracy graph shows a delivery line immediately (live grading continues daily)."""
    return await service.backfill_delivery(days, limit)


@router.post("/backfill-committed")
async def backfill_committed(days: int = 20, limit: int = 400):
    """Ask the scanner to reconstruct the high-conviction tier's accuracy history."""
    return await service.backfill_committed(days, limit)


@router.get("/auto-scan")
async def get_auto_scan():
    """Return whether the continuous background scan loop is enabled."""
    return await service.get_auto_scan()


@router.post("/auto-scan")
async def set_auto_scan(enabled: bool):
    """Enable or disable the continuous background scan loop."""
    return await service.set_auto_scan(enabled)


@router.get("/regime-detail")
async def regime_detail():
    """Full market-regime breakdown: raw indicators, conditions, and methodology."""
    return await service.regime_detail()


@router.get("/scan-status")
async def scan_status():
    """Centralized scan status (shared by Dashboard / Predictions / Portfolio):
    whether a sweep is running, progress, and when it last completed. Single source
    of truth so a rescan started on one page disables rescan everywhere."""
    return await service.scan_status()


# ── AI Loss Post-Mortem & Lessons ─────────────────────────────────────────────

@router.post("/loss-learning/run")
async def loss_learning_run(limit: int = 60, max_new: int = 15):
    """Analyse recent losing trades that don't yet have a post-mortem, store the
    AI explanations, and refresh the aggregated lessons."""
    return await service.loss_learning_run(limit, max_new)


@router.get("/loss-learning/postmortems")
async def loss_learning_postmortems(limit: int = 50):
    """Recent loss post-mortems (why each losing trade lost)."""
    return await service.loss_learning_postmortems(limit)


@router.get("/loss-learning/lessons")
async def loss_learning_lessons():
    """The aggregated lessons the AI has learned from losing trades."""
    return await service.loss_learning_lessons()


# ── Scanner post-market signal score (learning feedback) ──────────────────────

@router.post("/scan-feedback")
async def scan_feedback(req: ScanFeedback):
    """Receive the scanner's post-market grade and persist each pick's outcome so
    it feeds the system's learning record (and the signal-score history)."""
    return await service.scan_feedback(req)


@router.get("/scan-evaluation")
async def scan_evaluation():
    """Latest post-market signal-score grade + the accuracy trend over time.

    The detailed latest grade comes straight from the scanner (Redis); the trend
    is the per-day accuracy history persisted from each feedback push."""
    return await service.scan_evaluation()


# ── Autopilot ─────────────────────────────────────────────────────────────────

@router.get("/trade-gate")
async def get_trade_gate_endpoint():
    """Current trade-gate mode + the available presets (for the dashboard)."""
    return await service.get_trade_gate_endpoint()


@router.get("/watchlist-config")
async def get_watchlist_config():
    """Current intraday watchlist size (how many most-convicted picks are shown +
    graded for the signal score)."""
    return await service.get_watchlist_config()


@router.post("/watchlist-config")
async def set_watchlist_config(req: WatchlistConfigRequest):
    """Set how many most-convicted intraday picks the watchlist surfaces + grades
    (3–25). Applies on the next scan; trigger a Rescan to apply immediately."""
    return await service.set_watchlist_config(req)


@router.post("/trade-gate")
async def set_trade_gate_endpoint(req: TradeGateRequest):
    """Switch how selective session entries are (applies to paper, replay & autopilot)."""
    return await service.set_trade_gate_endpoint(req)


@router.get("/llm-status")
async def get_llm_status():
    """Which LLM provider is active (Anthropic vs Ollama) and whether it responds."""
    return await service.get_llm_status()


@router.get("/angel-status")
async def get_angel_status():
    """Angel One real-time feed status (configured? logged in? symbols live?)."""
    return await service.get_angel_status()


@router.get("/autopilot")
async def get_autopilot():
    return await service.get_autopilot()


@router.post("/autopilot")
async def set_autopilot(req: AutopilotRequest):
    return await service.set_autopilot(req)


@router.post("/autopilot/reset-cursor")
async def reset_autopilot_cursor():
    """Reset the backtest autopilot's next trade date to the last trading day
    before today (proxied to the autopilot microservice)."""
    return await service.reset_autopilot_cursor()


@router.post("/autopilot/batch-size")
async def set_autopilot_batch_size(req: BatchSizeRequest):
    """Change the backtest autopilot's concurrent-sessions-per-batch (1–50),
    proxied to the autopilot microservice. Takes effect on the next batch."""
    return await service.set_autopilot_batch_size(req)


@router.post("/autopilot/speed")
async def set_autopilot_speed(req: SpeedRequest):
    """Change the backtest autopilot's replay speed (candles/step, 1–120),
    proxied to the autopilot microservice. Applies to newly started sessions."""
    return await service.set_autopilot_speed(req)


@router.post("/autopilot/paper-timing")
async def set_autopilot_paper_timing(req: PaperTimingRequest):
    """Set the entry-timing mode for autopilot **paper** sessions. The autopilot
    reads this each tick, so new paper sessions open in the chosen mode (existing
    running sessions keep the mode they started with)."""
    return await service.set_autopilot_paper_timing(req)


# ── Learning curve (system getting smarter over time) ─────────────────────────

@router.get("/learning-curve")
async def learning_curve(source: str = "PAPER,LIVE,REPLAY", window: int = 50):
    """Learning curve as the system accumulates experience (trades ordered by time).

    Win-rate alone is misleading for an asymmetric-payoff strategy (small losses,
    large wins), so we return three aligned series plus a per-source breakdown:

      • cum_win_rate    — running win-rate over all trades so far (lagging)
      • roll_win_rate   — trailing-`window` win-rate (recency-sensitive: the real
                          "is it learning lately?" signal)
      • cum_equity      — cumulative sum of pnl_pct in % (the true profitability
                          curve; rises even when win-rate is < 50%)

    `source` is a comma list of PAPER/REPLAY/LIVE/BACKTEST. REPLAY (historical
    replays) usually dwarfs real PAPER/LIVE trades, so callers can isolate sources.
    """
    return await service.learning_curve(source, window)


# ── System events overlay (correlate curve moves with what changed) ────────────

@router.get("/learning-events")
async def list_learning_events():
    """System-update markers shown on the learning curve."""
    return await service.list_learning_events()


@router.post("/learning-events")
async def add_learning_event(req: LearningEventRequest):
    """Log a system change so its effect on the curve can be seen."""
    return await service.add_learning_event(req)


# ── Pattern Memory ────────────────────────────────────────────────────────────

@router.get("/memory/stats")
async def memory_stats():
    """Size + win-rate breakdown of the Pattern Memory bank (for the UI)."""
    return await service.memory_stats()


@router.delete("/memory/purge")
async def memory_purge(sources: str = "REPLAY"):
    """Delete memory cases by source label so the bank can rebuild from clean data.

    sources: comma-separated, e.g. 'REPLAY' or 'REPLAY,BACKTEST'.
    Intended for resetting after a bug-fix that invalidated old training runs.
    LIVE cases are never affected regardless of what is passed.
    """
    return await service.memory_purge(sources)


@router.post("/memory/query")
async def memory_query(req: AnalyzeRequest):
    """Inspect what the memory bank recalls for the situation in `candles`."""
    return await service.memory_query(req)


@router.post("/memory/sweep")
async def memory_sweep(background: bool = True):
    """Refresh the BACKTEST memory from fresh real backtests across the watchlist.

    Replaces (not appends) backtest cases so it stays bounded; LIVE cases are
    preserved. Runs automatically nightly — this triggers it on demand."""
    return await service.memory_sweep(background)


@router.get("/memory/sweep/status")
async def memory_sweep_status():
    """Last sweep summary + whether one is currently running."""
    return await service.memory_sweep_status()


# ── Pattern Recognition Model (dedicated, continuously-learning) ───────────────

@router.post("/pattern-model/train")
async def pattern_model_train(req: PatternTrainRequest, background: bool = True):
    """Train the pattern-recognition model from backtest history — patterns ONLY
    (fingerprint → realised forward move). Keeps the recogniser getting smarter."""
    return await service.pattern_model_train(req, background)


@router.get("/pattern-model/status")
async def pattern_model_status():
    """Training state + the model's current accuracy."""
    return await service.pattern_model_status()


@router.get("/pattern-model/curve")
async def pattern_model_curve(limit: int = 200):
    """The model's accuracy as it has learned (for the 'getting smarter' chart)."""
    return await service.pattern_model_curve(limit)


@router.get("/pattern-model/weights")
async def pattern_model_weights():
    """The learned weights — the scanner pulls these once per sweep to score each
    pattern locally and gate the high-conviction tier on the model's agreement."""
    return await service.pattern_model_weights()


@router.post("/pattern-model/predict")
async def pattern_model_predict(req: AnalyzeRequest):
    """What does the pattern model say about this candle window's *pattern* alone?"""
    return await service.pattern_model_predict(req)


@router.post("/pattern-model/grade")
async def pattern_model_grade(req: AnalyzeRequest):
    """Graded pattern signal (A/B/C/D) from the unified pattern engine — model P(up)
    blended with the memory bank's win-rate, plus a Monte-Carlo path forecast
    (projected target/stop + uncertainty). The same grading used to gate trades."""
    return await service.pattern_model_grade(req)


@router.post("/pattern-model/forecast")
async def pattern_model_forecast(req: AnalyzeRequest):
    """Monte-Carlo path forecast for a candle window — projected return path with an
    uncertainty band, data-driven target/stop (expected favourable/adverse
    excursion), P(up) and P(target-before-stop). CPU-only, no GPU/model weights."""
    return await service.pattern_model_forecast(req)


# ── AI model registry (independent enable/weight per model) ───────────────────

@router.get("/models")
async def list_models():
    """All independent AI models with their enable flag + weight override and
    human-facing metadata, for the AI Models control panel."""
    return await service.list_models()


@router.post("/models")
async def update_model(req: ModelConfigRequest):
    """Enable/disable a model or pin/clear its vote-weight override at runtime."""
    return await service.update_model(req)


@router.post("/gbm/train")
async def gbm_train(max_symbols: int = 250, horizon: int = 3, lookback_days: int = 365):
    """Train the Gradient-Boosted P(up) model on backfill (fingerprint → realised
    forward return) samples. Rotates through the universe across runs."""
    return await service.gbm_train(max_symbols, horizon, lookback_days)


@router.get("/gbm/status")
async def gbm_status():
    return await service.gbm_status()


@router.post("/memory/seed")
async def memory_seed(req: SeedMemoryRequest):
    """Bulk-seed the memory bank by replaying historical daily candles.

    For each sliding window we fingerprint the situation, look `horizon` bars
    ahead to measure the realised forward return, label the action by its sign,
    and store the case. This is the system's 'study' phase — it walks through
    history once so it starts live having already 'seen' thousands of setups.
    """
    return await service.memory_seed(req)


# ── Sentiment pipeline ────────────────────────────────────────────────────────

@router.post("/sentiment/refresh")
async def sentiment_refresh(symbol: str, force: bool = False):
    """Fetch fresh news headlines for a symbol, analyse with LLM, write to Redis.

    The SentimentAgent reads `ai_engine:sentiment:{SYMBOL}` on every analyze call.
    This endpoint lets the UI (or a scheduler) pre-warm that key so the agent
    has a real signal to vote on.

    force=true skips the 15-minute cache TTL and re-fetches unconditionally.
    """
    return await service.sentiment_refresh(symbol, force)


@router.post("/sentiment/historical/bulk")
async def sentiment_historical_bulk(payload: dict):
    """Pre-fetch historical news sentiment for a list of symbols on a specific date.

    Used by the backtest autopilot before starting a queue: it ranks all candidate
    symbols by sentiment score for the backtest date so only the most bullish/
    high-conviction stocks are selected.

    Request body: {"symbols": ["SBIN", "HDFCBANK", ...], "date": "YYYY-MM-DD"}
    Response: {"data": {"SBIN": {sentiment, score, confidence, catalyst, ...}, ...}}
    """
    return await service.sentiment_historical_bulk(payload)


@router.get("/sentiment/historical/{symbol}/{date}")
async def sentiment_historical_read(symbol: str, date: str):
    """Read (or lazily fetch) cached historical sentiment for a symbol on a given date."""
    return await service.sentiment_historical_read(symbol, date)


@router.get("/sentiment/{symbol}")
async def sentiment_read(symbol: str):
    """Read the current cached sentiment for a symbol (no re-fetch)."""
    return await service.sentiment_read(symbol)


@router.get("/exit-variants")
async def exit_variants(days: int = 14):
    """Exit-policy A/B results: every EXIT_VARIANT simulated nightly on the same
    entry population against the recorded tick data (see agents/counterfactual).
    This is the evidence table for changing the live exit policy."""
    from app.agents.counterfactual import exit_ab_report
    return {"status": "success", "data": await exit_ab_report(days)}
