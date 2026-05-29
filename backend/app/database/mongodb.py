"""
MongoDB Database Setup
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

logger = logging.getLogger(__name__)

client: AsyncIOMotorClient = None
db: AsyncIOMotorDatabase = None


async def init_mongodb():
    """Initialize MongoDB connection"""
    global client, db

    try:
        logger.info(f"Connecting to MongoDB at {settings.MONGODB_HOST}...")

        client = AsyncIOMotorClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000
        )

        # Test connection
        await client.admin.command('ping')

        db = client[settings.MONGODB_DB]

        # Create collections if they don't exist
        collections = ['predictions', 'prediction_history', 'news', 'sentiment', 'chat_history']
        existing = await db.list_collection_names()

        for collection_name in collections:
            if collection_name not in existing:
                await db.create_collection(collection_name)
                logger.info(f"Created collection: {collection_name}")

        logger.info("✅ MongoDB initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize MongoDB: {str(e)}")
        raise


async def close_mongodb():
    """Close MongoDB connection"""
    global client
    if client:
        client.close()
        logger.info("✅ MongoDB connection closed")


def get_mongodb() -> AsyncIOMotorDatabase:
    """Get MongoDB database instance"""
    if not db:
        raise RuntimeError("MongoDB not initialized")
    return db
