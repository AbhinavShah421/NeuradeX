import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from app.config import settings
from app.consumer import start_consuming

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.agent_bootstrap import connect_with_retry, health_payload
from app.cors import configure_cors

_pool: asyncpg.Pool | None = None
_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _consumer_task
    logger.info("technical-agent starting")

    _pool = await connect_with_retry(
        lambda: asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=8),
        what="technical-agent postgres",
        required=True,
    )

    _consumer_task = asyncio.create_task(
        start_consuming(settings.RABBITMQ_URL, _pool),
        name="technical-consumer",
    )
    logger.info("technical-agent ready — consuming market.data.technical")
    yield

    if _consumer_task:
        _consumer_task.cancel()
        await asyncio.gather(_consumer_task, return_exceptions=True)
    if _pool:
        await _pool.close()
    logger.info("technical-agent shut down")


app = FastAPI(title="NeuradeX — Technical Agent", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    return health_payload(settings.SERVICE_NAME, agent=settings.AGENT_NAME, db_pool=_pool is not None)
