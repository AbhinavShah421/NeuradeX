"""
Backtesting Module — simulate trading strategies on Groww historical data.

Strategies: SMA Crossover, RSI Mean Reversion, MACD Crossover, Bollinger Band.
Also provides a live-signal endpoint for paper trading.
Day-Autopilot: AI-driven intraday simulation with LLM decision-making.

This is a thin FastAPI router: it parses/validates requests and delegates all
business logic (DB/data-provider access, calculations, simulation) to
app.services.backtest_service. See that module's docstring for the list of
names re-exported below for other modules that import internals from here.
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel  # noqa: F401  (kept for readability / potential future use)

from app.utils.elk_logger import get_logger
from app.services import backtest_service as service

# Pydantic request models — defined in the service module, imported here for
# use as route parameter types. Schema is unchanged, only the module moved.
from app.services.backtest_service import (
    BacktestRequest,
    DayAutopilotRequest,
    AgentStepRequest,
    ProgressiveStartRequest,
    ProgressiveStepRequest,
)

# Re-exported for external consumers that import these names directly from
# app.api.backtest — do not remove without updating those call sites:
#   app.api.paper_trading, app.api.sessions, app.agents.pattern_model,
#   app.agents.memory_sweep
from app.services.backtest_service import (  # noqa: F401
    IST,
    STRATEGIES,
    _MARKET_OPEN_MINUTES,
    _SQUAREOFF_MINUTES,
    _compute_metrics,
    _intraday_indicators,
    _llm_decide,
    _minutes_to_time,
    _tech_signal,
    _time_to_minutes,
    _fetch_candles,
    _run_engine,
    _build_trade_record,
    _derive_agent_signals,
    _save_backtest_trades,
    _prev_trading_day,
    _fetch_full_day_candles,
    _no_real_intraday_msg,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get("/strategies")
async def get_strategies():
    return await service.get_strategies_data()


@router.get("/providers")
async def get_data_providers():
    """List configured market-data providers and whether each is currently usable."""
    return await service.get_data_providers()


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    return await service.run_backtest(req)


@router.get("/live-signal/{symbol}")
async def get_live_signal(
    symbol: str,
    strategy: str  = Query("sma_crossover"),
    sma_fast: int  = Query(20), sma_slow: int = Query(50),
    rsi_period: int = Query(14), oversold: int = Query(30), overbought: int = Query(70),
    fast: int  = Query(12), slow: int = Query(26), signal: int = Query(9),
    window: int = Query(20), std_dev: float = Query(2.0),
):
    """Current live trading signal + indicator values for paper trading."""
    return await service.get_live_signal(
        symbol, strategy,
        sma_fast, sma_slow,
        rsi_period, oversold, overbought,
        fast, slow, signal,
        window, std_dev,
    )


@router.post("/day-autopilot")
async def day_autopilot(req: DayAutopilotRequest):
    """AI-powered intraday day trading simulation for a single date."""
    return await service.day_autopilot(req)


@router.get("/intraday-candles/{symbol}")
async def get_intraday_candles(
    symbol: str,
    date: str = Query(...),
    real_only: bool = Query(False, description="If true, 422 instead of simulating when Groww has no data"),
):
    """Return all 5-min candles for one trading day (Groww or simulated).
    No LLM involved — the frontend drives the progressive replay.
    With real_only=true the endpoint refuses to simulate."""
    return await service.get_intraday_candles(symbol, date, real_only)


@router.post("/agent-step")
async def agent_step(req: AgentStepRequest):
    """Single LLM decision step using only the candles seen so far.
    The agent has zero lookahead — it cannot see future price data."""
    return await service.agent_step(req)


@router.post("/progressive/start")
async def progressive_start(req: ProgressiveStartRequest):
    """Start a progressive backtest session.

    Fetches all 5-min candles from market open to start_time, runs the AI agent
    on the last candle, optionally executes the first trade, and returns the
    initial session state the client must persist and pass back each step.
    """
    return await service.progressive_start(req)


@router.post("/progressive/step")
async def progressive_step(req: ProgressiveStepRequest):
    """Advance the progressive backtest by one 5-min candle.

    The client passes its full session state (cash, position, trades, etc.).
    The server fetches fresh candles from Groww from market open up to the
    next candle time, runs the AI agent, executes any trade, and returns the
    updated state the client should persist for the following step.
    """
    return await service.progressive_step(req)
