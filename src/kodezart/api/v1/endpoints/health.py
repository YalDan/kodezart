"""GET /api/v1/health — top of the pattern chain."""

from fastapi import APIRouter

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.handlers.health_handler import HealthHandler
from kodezart.types.responses.common import BaseResponse

router = APIRouter()
_handler = HealthHandler()
_log: BoundLogger = get_logger(__name__)


@router.get("/health", response_model=BaseResponse, summary="Health check")
async def get_health() -> BaseResponse:
    """``GET /api/v1/health`` endpoint. Returns BaseResponse with HealthStatus."""
    await _log.adebug("health_check_requested")
    return await _handler.handle()
