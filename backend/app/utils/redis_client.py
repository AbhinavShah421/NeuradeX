"""
Redis Cache Setup
"""

import logging
import redis.asyncio as redis
from app.config import settings

logger = logging.getLogger(__name__)

redis_client = None


async def init_redis():
    """Initialize Redis connection"""
    global redis_client
    
    try:
        logger.info(f"Connecting to Redis at {settings.REDIS_HOST}...")
        
        redis_client = await redis.from_url(
            settings.REDIS_URL,
            encoding="utf8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        
        # Test connection
        await redis_client.ping()
        
        logger.info("✅ Redis initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Redis: {str(e)}")
        raise


async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("✅ Redis connection closed")


def get_redis():
    """Get Redis client"""
    if not redis_client:
        raise RuntimeError("Redis not initialized")
    return redis_client


async def cache_get(key: str):
    """Get value from cache"""
    try:
        client = get_redis()
        return await client.get(key)
    except Exception as e:
        logger.warning(f"Cache get error: {str(e)}")
        return None


async def cache_set(key: str, value: str, expire: int = 3600):
    """Set value in cache"""
    try:
        client = get_redis()
        await client.setex(key, expire, value)
    except Exception as e:
        logger.warning(f"Cache set error: {str(e)}")


async def cache_delete(key: str):
    """Delete value from cache"""
    try:
        client = get_redis()
        await client.delete(key)
    except Exception as e:
        logger.warning(f"Cache delete error: {str(e)}")
