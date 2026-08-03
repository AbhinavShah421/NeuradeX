"""
PostgreSQL Database Setup
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

# create_all() only creates missing tables — it never adds columns to a table
# that already exists. Columns added to a model after first deploy need an
# explicit DDL statement here.
_COLUMN_MIGRATIONS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS broker_key_type VARCHAR(20) DEFAULT 'approval'",
)

# Shared declarative base — all ORM models must import this
Base = declarative_base()

engine = None
AsyncSessionLocal = None


async def init_postgres():
    """Initialize PostgreSQL database"""
    global engine, AsyncSessionLocal

    try:
        logger.info(f"Connecting to PostgreSQL at {settings.POSTGRES_HOST}...")

        engine = create_async_engine(
            settings.POSTGRES_URL,
            echo=False,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=0,
        )

        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Import models so their tables are registered on Base.metadata
        import app.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=True))
            for ddl in _COLUMN_MIGRATIONS:
                await conn.execute(text(ddl))

        logger.info("✅ PostgreSQL initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize PostgreSQL: {str(e)}")
        raise


async def close_postgres():
    """Close PostgreSQL connection"""
    global engine
    if engine:
        await engine.dispose()
        logger.info("✅ PostgreSQL connection closed")


async def get_db() -> AsyncSession:
    """Get database session"""
    if not AsyncSessionLocal:
        raise RuntimeError("Database not initialized")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
