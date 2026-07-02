"""Model Trainer service — trains XGBoost + PPO RL models, registers to MLflow."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from .config import settings
from .consumer import run_consumer
from .db import fetch_all_symbols
from .trainers.xgboost_trainer import train_xgboost
from .trainers.rl_trainer import train_rl
from .trainers.meta_trainer import train_meta_model
from .trainers.calibration_trainer import train_calibrator

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.cors import configure_cors

_background_tasks: list[asyncio.Task] = []
_last_train_time: datetime | None = None
_train_lock = asyncio.Lock()


async def _run_all_trainers(trigger: str = "scheduled", symbols: list[str] | None = None) -> dict:
    global _last_train_time
    async with _train_lock:
        syms = symbols or settings.watchlist_symbols
        logger.info("Starting training run (trigger=%s, symbols=%d)", trigger, len(syms))

        limit = settings.TRAIN_DAYS
        all_rows = await fetch_all_symbols(settings.POSTGRES_URL, syms, interval="1d", limit=limit)

        xgb_ok = await train_xgboost(
            all_rows,
            mlflow_uri=settings.MLFLOW_TRACKING_URI,
            model_name=settings.XGBOOST_MODEL_NAME,
            min_accuracy=settings.MIN_TRAIN_ACCURACY,
        )

        rl_ok = await train_rl(
            all_rows,
            mlflow_uri=settings.MLFLOW_TRACKING_URI,
            model_name=settings.RL_MODEL_NAME,
            min_sharpe=settings.MIN_SHARPE_RATIO,
            timesteps=settings.RL_TIMESTEPS,
        )

        meta_ok = await train_meta_model(
            postgres_url=settings.POSTGRES_URL,
            mlflow_uri=settings.MLFLOW_TRACKING_URI,
        )

        calib_ok = await train_calibrator(
            postgres_url=settings.POSTGRES_URL,
            mlflow_uri=settings.MLFLOW_TRACKING_URI,
        )

        _last_train_time = datetime.utcnow()
        result = {
            "xgboost_registered": xgb_ok,
            "rl_registered": rl_ok,
            "meta_registered": meta_ok,
            "calibrator_registered": calib_ok,
            "trigger": trigger,
        }
        logger.info("Training complete: %s", result)
        return result


async def _scheduled_retrain_loop() -> None:
    interval_secs = settings.RETRAIN_SCHEDULE_HOURS * 3600
    await asyncio.sleep(60)
    while True:
        try:
            await _run_all_trainers(trigger="scheduled")
        except Exception as exc:
            logger.error("Scheduled retrain error: %s", exc)
        await asyncio.sleep(interval_secs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("model-trainer starting")
    _background_tasks.append(asyncio.create_task(_scheduled_retrain_loop()))
    _background_tasks.append(
        asyncio.create_task(run_consumer(settings.RABBITMQ_URL, _run_all_trainers))
    )
    yield
    for t in _background_tasks:
        t.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    logger.info("model-trainer stopped")


app = FastAPI(title="model-trainer", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "last_train": _last_train_time.isoformat() if _last_train_time else None,
        "mlflow_uri": settings.MLFLOW_TRACKING_URI,
    }


@app.post("/train")
async def trigger_train(trigger: str = "manual"):
    if _train_lock.locked():
        return {"status": "already_running"}
    asyncio.create_task(_run_all_trainers(trigger=trigger))
    return {"status": "started", "trigger": trigger}


@app.get("/status")
async def status():
    return {
        "training_active": _train_lock.locked(),
        "last_train": _last_train_time.isoformat() if _last_train_time else None,
        "watchlist": settings.watchlist_symbols,
        "xgboost_model": settings.XGBOOST_MODEL_NAME,
        "rl_model": settings.RL_MODEL_NAME,
    }
