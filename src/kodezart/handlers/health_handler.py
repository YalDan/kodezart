"""Health handler — no logic; pure delegation."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.services.health_service import HealthService
from kodezart.types.responses.common import BaseResponse


class HealthHandler:
    """Request handler for the health endpoint."""

    def __init__(self) -> None:
        self._service = HealthService()
        self._log: BoundLogger = get_logger(__name__)

    async def handle(self) -> BaseResponse:
        """Delegate to HealthService and return BaseResponse."""
        await self._log.adebug("health_handler_delegating")
        return await self._service.check()
