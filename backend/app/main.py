"""
Main FastAPI Application
NeuradeX System
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
import socketio
from app.config import settings
from app.database.mongodb import init_mongodb, close_mongodb
from app.database.postgres import init_postgres, close_postgres
from app.utils.redis_client import init_redis, close_redis
from app.api import stocks, predictions, portfolio, risk, orders, agent, backtest, auth, paper_trading, ai_engine, mlflow_proxy
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
    logger.info("Starting NeuradeX System", extra={"log_type": "app_lifecycle", "event": "startup"})

    try:
        await init_postgres()
        await init_mongodb()
        await init_redis()

        if settings.GROWW_API_KEY and settings.GROWW_API_SECRET:
            init_groww_client(settings.GROWW_API_KEY, settings.GROWW_API_SECRET)
            logger.info("Groww API client ready", extra={"log_type": "app_lifecycle", "event": "groww_init"})
        else:
            logger.warning(
                "GROWW_API_KEY not set — stock data will use simulation",
                extra={"log_type": "app_lifecycle", "event": "groww_skipped"},
            )

        logger.info("Loading ML models", extra={"log_type": "app_lifecycle", "event": "ml_init"})
        await initialize_ml_models()

        # Nightly pattern-memory refresh (replays real backtests after market close)
        try:
            from app.agents.memory_sweep import scheduled_sweep_loop
            app.state.memory_sweep_task = asyncio.create_task(scheduled_sweep_loop())
            logger.info("Pattern-memory nightly sweep scheduled",
                        extra={"log_type": "app_lifecycle", "event": "memory_sweep_scheduled"})
        except Exception as exc:
            logger.warning("Could not schedule memory sweep: %s", exc)

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
        task = getattr(app.state, "memory_sweep_task", None)
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
