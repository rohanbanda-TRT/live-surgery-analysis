"""
MongoDB database connection and management using PyMongo Async API.
Note: Motor is deprecated as of May 2025, using PyMongo Async API instead.
"""
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from typing import Optional
from contextlib import asynccontextmanager
from app.core.config import settings
from app.core.logging import logger


class MongoDB:
    """MongoDB connection manager."""
    
    client: Optional[AsyncMongoClient] = None
    database: Optional[AsyncDatabase] = None
    
    @classmethod
    async def connect(cls):
        """Establish connection to MongoDB."""
        try:
            cls.client = AsyncMongoClient(
                settings.MONGODB_URL,
                minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
                maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
            )
            cls.database = cls.client[settings.MONGODB_DB_NAME]
            
            # Ping database to verify connection
            await cls.database.command("ping")
            logger.info("mongodb_connected", database=settings.MONGODB_DB_NAME)
            
        except Exception as e:
            logger.error("mongodb_connection_failed", error=str(e))
            raise
    
    @classmethod
    async def disconnect(cls):
        """Close MongoDB connection."""
        if cls.client:
            cls.client.close()
            logger.info("mongodb_disconnected")
    
    @classmethod
    def get_database(cls) -> AsyncDatabase:
        """Get database instance."""
        if cls.database is None:
            raise RuntimeError("Database not initialized. Call connect() first.")
        return cls.database


async def get_db() -> AsyncDatabase:
    """
    Dependency to get database instance for route handlers.
    
    Usage in routes:
        @router.get("/items")
        async def get_items(db: AsyncDatabase = Depends(get_db)):
            ...
    """
    return MongoDB.get_database()
