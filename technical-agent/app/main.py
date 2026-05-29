import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.consumer import start_consuming

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _consumer_task
    logger.info("technical-agent starting")

    for attempt in range(1, 11):
        try:
            _pool = await asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=8)
            break
        except Exception as exc:
            logger.warning("DB connect attempt %d/10: %s", attempt, exc)
            await asyncio.sleep(min(2 ** attempt, 30))

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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "agent": settings.AGENT_NAME}
