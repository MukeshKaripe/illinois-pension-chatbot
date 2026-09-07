"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
async def health_check() -> dict:
    """Liveness/readiness probe for Container Apps."""
    return {
        "status": "healthy",
        "service": "pension-chat",
        "version": "0.1.0",
    }
