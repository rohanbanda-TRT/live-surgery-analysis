"""
Health check endpoints.
"""
from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase
from app.db.mongodb import get_db
from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncDatabase = Depends(get_db)):
    """
    Health check endpoint for load balancers and monitoring.
    Verifies database connectivity.
    """
    try:
        # Ping database
        await db.command("ping")
        
        return {
            "status": "healthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "database": "connected"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "database": "disconnected",
            "error": str(e)
        }


@router.get("/ready")
async def readiness_check(db: AsyncDatabase = Depends(get_db)):
    """
    Readiness check for Kubernetes or other orchestrators.
    """
    try:
        await db.command("ping")
        return {"ready": True}
    except Exception:
        return {"ready": False}
