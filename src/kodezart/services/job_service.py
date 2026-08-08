"""Job status service — registry facts plus checkpoint-derived facts."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobRegistry, RunStateReader
from kodezart.types.responses.job import JobStatusResponse, RunStateResponse


class JobService:
    """Composes a job's registry record with its checkpointed run state.

    ``run_state_reader`` is ``None`` when the deployment persists no run
    state at all.  That is a different answer from a reader that finds no
    checkpoint for this job yet, and the two are never collapsed.
    """

    def __init__(
        self,
        *,
        registry: JobRegistry,
        run_state_reader: RunStateReader | None,
    ) -> None:
        self._registry: JobRegistry = registry
        self._run_state_reader: RunStateReader | None = run_state_reader
        self._log: BoundLogger = get_logger(__name__)

    async def get_status(self, *, job_id: str) -> JobStatusResponse | None:
        """Status for *job_id*, or ``None`` when the job is unknown."""
        record = await self._registry.get(job_id=job_id)
        if record is None:
            await self._log.adebug("job_status_unknown", job_id=job_id)
            return None

        run: RunStateResponse | None = None
        if self._run_state_reader is not None:
            state = await self._run_state_reader.read(job_id=job_id)
            if state is not None:
                run = RunStateResponse.model_validate(state, from_attributes=True)

        return JobStatusResponse(
            job_id=record.job_id,
            lane=record.lane,
            state=record.state,
            queue_position=record.queue_position,
            submitted_at=record.submitted_at,
            outcome=record.outcome,
            truncated=record.truncated,
            run_state_available=self._run_state_reader is not None,
            run=run,
        )
