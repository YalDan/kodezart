"""Agent handler — unpacks request models, delegates to service."""

import sys
import uuid
from collections.abc import AsyncGenerator

from kodezart.core.error_egress import build_error_event
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, JobQueue
from kodezart.types.domain.agent import JobAcceptedEvent
from kodezart.types.domain.job import JobRecord
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.requests.agent import QueryRequest


class AgentHandler:
    """Request handler for agent endpoints.

    Unpacks request models, delegates to ``AgentRunner``/``JobQueue``,
    and serializes events for SSE streaming.  Serialization of agent
    events happens here and nowhere else.
    """

    def __init__(
        self,
        service: AgentRunner,
        skills: SkillsSelection,
        queue: JobQueue | None = None,
    ) -> None:
        self._service = service
        self._skills: SkillsSelection = skills
        self._queue = queue
        self._log: BoundLogger = get_logger(__name__)

    async def stream_query(
        self,
        request: QueryRequest,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Stream agent query events as serialized dicts."""
        await self._log.adebug("agent_query_requested")
        try:
            cache_key = uuid.uuid4().hex
            output_format: dict[str, object] | None = (
                {"type": "json_schema", "schema": request.output_schema}
                if request.output_schema is not None
                else None
            )
            async for event in self._service.stream(
                prompt=request.prompt,
                repo_path=request.repo_path,
                repo_url=request.repo_url,
                branch=request.branch,
                permission_mode=request.permission_mode,
                allowed_tools=request.allowed_tools,
                skills=self._skills,
                session_type=SessionType.API_QUERY,
                session_id=request.session_id,
                output_format=output_format,
                cache_key=cache_key,
            ):
                yield event.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            cause = exc.__cause__
            # ``exc_info=sys.exc_info()`` is passed explicitly to harden
            # against async-executor context loss (hynek/structlog#488
            # class).  Structlog's ``aexception`` auto-attaches
            # ``exc_info`` in the simple case, but defensive explicit
            # plumbing matters here because the LangGraph executor may
            # consume the exception context before the log call runs.
            await self._log.aexception(
                "stream_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
                cause=type(cause).__name__ if cause is not None else None,
                exc_info=sys.exc_info(),
            )
            yield build_error_event(exc).model_dump(
                by_alias=True,
                exclude_none=True,
            )

    async def attach_job(
        self,
        *,
        job_id: str,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Stream an already-queued job's events as serialized dicts."""
        await self._log.adebug("agent_job_attach_requested", job_id=job_id)
        try:
            if self._queue is None:
                msg = "Job queue not configured"
                raise RuntimeError(msg)
            async for event in self._queue.attach(job_id=job_id):
                yield event.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            cause = exc.__cause__
            await self._log.aexception(
                "stream_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
                cause=type(cause).__name__ if cause is not None else None,
                exc_info=sys.exc_info(),
            )
            yield build_error_event(exc).model_dump(
                by_alias=True,
                exclude_none=True,
            )

    async def stream_workflow(
        self,
        *,
        record: JobRecord,
        status_url: str,
        stream_url: str,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Emit the ``job_accepted`` frame, then attach to the queued run."""
        queue_position = record.queue_position
        if queue_position is None:
            msg = f"accepted job {record.job_id} carries no queue position"
            raise RuntimeError(msg)
        yield JobAcceptedEvent(
            job_id=record.job_id,
            lane=record.lane,
            queue_position=queue_position,
            status_url=status_url,
            stream_url=stream_url,
        ).model_dump(by_alias=True, exclude_none=True)
        async for payload in self.attach_job(job_id=record.job_id):
            yield payload
