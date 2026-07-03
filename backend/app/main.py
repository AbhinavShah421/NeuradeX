"""
Main FastAPI Application
NeuradeX System
"""

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
import socketio
from app.config import settings
from app.database.mongodb import init_mongodb, close_mongodb
from app.database.postgres import init_postgres, close_postgres
from app.utils.redis_client import init_redis, close_redis
from app.api import stocks, predictions, portfolio, risk, orders, agent, backtest, auth, paper_trading, ai_engine, mlflow_proxy, sessions, user_settings, mutual_funds, delivery_paper, live_trading, system, recordings
from app.websocket.socket_manager import sio
from app.ml_core.initializer import initialize_ml_models
from app.utils.groww_client import init_groww_client
from app.utils.elk_logger import setup_logging, get_logger
from app.middleware.logging_middleware import RequestLoggingMiddleware

# Bootstrap structured JSON logging → Elasticsearch + stdout
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # BACKEND_ROLE controls which background tasks start in this process:
    #   "api"    → HTTP-only; no session runner / ML sweeps (low CPU / memory)
    #   "runner" → session runner + ML sweeps only; no HTTP routes exposed
    #   "full"   → everything (default, single-container mode)
    _role = os.getenv("BACKEND_ROLE", "full").lower()
    logger.info("Starting NeuradeX System (role=%s)", _role,
                extra={"log_type": "app_lifecycle", "event": "startup", "role": _role})

    try:
        await init_postgres()
        await init_mongodb()
        await init_redis()

        # Load Groww credentials from DB (set via the UI). Never from .env.
        try:
            from sqlalchemy import text
            from app.database.postgres import AsyncSessionLocal
            async with AsyncSessionLocal() as _db:
                row = await _db.execute(
                    text("SELECT broker_api_key, broker_api_secret FROM users WHERE broker_api_key IS NOT NULL AND broker_api_key != '' LIMIT 1")
                )
                db_creds = row.fetchone()
            if db_creds and db_creds[0] and db_creds[1]:
                init_groww_client(db_creds[0], db_creds[1])
                logger.info("Groww API client ready (credentials from DB)", extra={"log_type": "app_lifecycle", "event": "groww_init"})
            else:
                logger.warning(
                    "No Groww credentials in DB — update them via the UI. Stock data will use simulation.",
                    extra={"log_type": "app_lifecycle", "event": "groww_skipped"},
                )
        except Exception as exc:
            logger.warning("Could not load Groww credentials from DB: %s", exc,
                           extra={"log_type": "app_lifecycle", "event": "groww_skipped"})

        logger.info("Loading ML models", extra={"log_type": "app_lifecycle", "event": "ml_init"})
        await initialize_ml_models()

        # ── Background tasks: only start in the appropriate role ─────────────
        # "api" role skips CPU/memory-heavy background work so the HTTP server
        # stays responsive. "runner" role skips routes but runs the compute tasks.
        # "full" (default) starts everything — single-container mode.

        if _role in ("full", "runner"):
            # Nightly pattern-memory refresh (replays real backtests after market close)
            try:
                from app.agents.memory_sweep import scheduled_sweep_loop
                app.state.memory_sweep_task = asyncio.create_task(scheduled_sweep_loop())
                logger.info("Pattern-memory nightly sweep scheduled",
                            extra={"log_type": "app_lifecycle", "event": "memory_sweep_scheduled"})
            except Exception as exc:
                logger.warning("Could not schedule memory sweep: %s", exc)

            # Gradient-Boosted P(up) model nightly auto-retrain
            try:
                from app.agents.pattern_model import gbm_autotrain_loop
                app.state.gbm_autotrain_task = asyncio.create_task(gbm_autotrain_loop())
                logger.info("GBM nightly auto-retrain scheduled",
                            extra={"log_type": "app_lifecycle", "event": "gbm_autotrain_scheduled"})
            except Exception as exc:
                logger.warning("Could not schedule GBM auto-retrain: %s", exc)

            # Background runner that advances live trading sessions server-side
            try:
                from app.api.sessions import session_runner_loop
                app.state.session_runner_task = asyncio.create_task(session_runner_loop())
                logger.info("Live session runner scheduled",
                            extra={"log_type": "app_lifecycle", "event": "session_runner_scheduled"})
            except Exception as exc:
                logger.warning("Could not start session runner: %s", exc)

            # Continuous 1-second tick capture → our own real-data dataset (Parquet).
            # Single writer (runner/full role only) so there are no cross-container
            # Parquet races.
            try:
                from app.data.candle_capture import candle_capture_loop
                app.state.candle_capture_task = asyncio.create_task(candle_capture_loop())
                logger.info("1s candle capture scheduled",
                            extra={"log_type": "app_lifecycle", "event": "candle_capture_scheduled"})
            except Exception as exc:
                logger.warning("Could not start candle capture: %s", exc)

            # Recordings maintenance — keeps the capture allowlist in sync with the
            # scheduled/active recordings across day rollovers.
            try:
                from app.api.recordings import recordings_maintenance_loop
                app.state.recordings_maint_task = asyncio.create_task(recordings_maintenance_loop())
                logger.info("recordings maintenance scheduled",
                            extra={"log_type": "app_lifecycle", "event": "recordings_maint_scheduled"})
            except Exception as exc:
                logger.warning("Could not start recordings maintenance: %s", exc)

        if _role in ("full", "api"):
            # Delivery (multi-day) paper-trading autopilot — ticks once a day
            try:
                from app.api.delivery_paper import delivery_autopilot_loop
                app.state.delivery_paper_task = asyncio.create_task(delivery_autopilot_loop())
                logger.info("Delivery paper autopilot scheduled",
                            extra={"log_type": "app_lifecycle", "event": "delivery_paper_scheduled"})
            except Exception as exc:
                logger.warning("Could not schedule delivery paper autopilot: %s", exc)

        # Auto-squareoff loop — closes all MIS positions at 3:10 PM IST every trading day
        try:
            from app.api.live_trading import _auto_squareoff_loop
            app.state.live_squareoff_task = asyncio.create_task(_auto_squareoff_loop())
            logger.info("Live trading auto-squareoff scheduled",
                        extra={"log_type": "app_lifecycle", "event": "live_squareoff_scheduled"})
        except Exception as exc:
            logger.warning("Could not start live squareoff loop: %s", exc)

        # Autopilot now runs as its own microservice (autopilot-service:8015) — it
        # owns the paper + backtest training loops and starts sessions via the API.
        # The backend only reads/writes the enable flags and serves status.

        # Angel One (SmartAPI) real-time LTP feed for paper trading (optional —
        # only starts if ANGEL_* credentials are configured; else Yahoo is used).
        try:
            from app.utils.angel_client import init_angel_client, angel_poll_loop
            if init_angel_client():
                app.state.angel_task = asyncio.create_task(angel_poll_loop())
                logger.info("Angel One live feed scheduled",
                            extra={"log_type": "app_lifecycle", "event": "angel_scheduled"})
        except Exception as exc:
            logger.warning("Could not start Angel One feed: %s", exc)

        logger.info(
            "Application startup complete",
            extra={"log_type": "app_lifecycle", "event": "startup_complete"},
        )
    except Exception as e:
        logger.error(
            "Startup error",
            extra={"log_type": "app_lifecycle", "event": "startup_error", "error": str(e)},
            exc_info=True,
        )
        raise

    yield

    logger.info("Shutting down application", extra={"log_type": "app_lifecycle", "event": "shutdown"})
    try:
        for _attr in ("memory_sweep_task", "gbm_autotrain_task", "delivery_paper_task",
                      "session_runner_task", "candle_capture_task", "scanner_task",
                      "autopilot_task", "angel_task", "live_squareoff_task"):
            task = getattr(app.state, _attr, None)
            if task:
                task.cancel()
        await close_postgres()
        await close_mongodb()
        await close_redis()
        logger.info("Cleanup complete", extra={"log_type": "app_lifecycle", "event": "shutdown_complete"})
    except Exception as e:
        logger.error(
            "Shutdown error",
            extra={"log_type": "app_lifecycle", "event": "shutdown_error", "error": str(e)},
            exc_info=True,
        )


# Create FastAPI app
app = FastAPI(
    title="NeuradeX",
    description="AI-powered real-time market intelligence platform",
    version="1.0.0",
    lifespan=lifespan,
)

# Request/response logging (must be added before CORS so request_id is set early)
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZIP compression
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/")
async def root():
    return {"message": "NeuradeX System", "status": "running", "docs": "/docs"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "stock-prediction-api"}


# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(risk.router, prefix="/api/risk", tags=["risk"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(paper_trading.router, prefix="/api/paper-trading", tags=["paper-trading"])
app.include_router(ai_engine.router, prefix="/api/ai-engine", tags=["ai-engine"])
app.include_router(mlflow_proxy.router, prefix="/api/mlflow", tags=["mlflow"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(user_settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(mutual_funds.router, prefix="/api/mutual-funds", tags=["mutual-funds"])
app.include_router(delivery_paper.router, prefix="/api/delivery-paper", tags=["delivery-paper"])
app.include_router(live_trading.router, prefix="/api/live-trading", tags=["live-trading"])
app.include_router(system.router, prefix="/api/system", tags=["system"])

# Socket.IO ASGI app
app_sio = socketio.ASGIApp(sio, app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app_sio",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level="info",
    )
