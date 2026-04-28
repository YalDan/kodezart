"""Health service — orchestrates domain logic; translates to BaseResponse."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.health import check_health
from kodezart.types.domain.health import HealthStatus
from kodezart.types.responses.common import BaseResponse


class HealthService:
    """Health check service wrapping domain logic in BaseResponse."""

    def __init__(self) -> None:
        self._log: BoundLogger = get_logger(__name__)

    async def check(self) -> BaseResponse:
        """Run health check and return BaseResponse with HealthStatus data."""
        status: HealthStatus = check_health()
        await self._log.ainfo("health_check_complete", healthy=status.healthy)
        return BaseResponse(
            success=status.healthy,
            data=status.model_dump(by_alias=True),
        )
