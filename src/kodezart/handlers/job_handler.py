"""Job handler — no logic; pure delegation."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.services.job_service import JobService
from kodezart.types.responses.job import JobStatusResponse


class JobHandler:
    """Request handler for the job status endpoint."""

    def __init__(self, service: JobService) -> None:
        self._service: JobService = service
        self._log: BoundLogger = get_logger(__name__)

    async def get_status(self, *, job_id: str) -> JobStatusResponse | None:
        """Delegate to JobService; ``None`` when the job is unknown."""
        await self._log.adebug("job_handler_delegating", job_id=job_id)
        return await self._service.get_status(job_id=job_id)
