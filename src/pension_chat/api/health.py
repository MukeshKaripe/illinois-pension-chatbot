"""Health check endpoint."""

from fastapi import APIRouter

from pension_chat.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
async def health_check() -> dict:
    """Liveness/readiness probe for Container Apps."""
    return {
        "status": "healthy",
        "service": "pension-chat",
        "version": "0.1.0",
        # "pbms" when employee records come from the fund; the UI shows the
        # people chips only then.
        "record_source": get_settings().record_source,
    }
