"""SSE streaming endpoints for agent execution."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from kodezart.core.config import AppConfig
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue
from kodezart.domain.errors import QueueFullError
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.types.domain.job import JobRecord
from kodezart.types.requests.agent import QueryRequest, WorkflowRequest
from kodezart.types.responses.common import BaseResponse
from kodezart.types.responses.job import FireAcceptedResponse
from kodezart.utils.sse import format_sse

router = APIRouter()
_log: BoundLogger = get_logger(__name__)


def _job_urls(config: AppConfig, job_id: str) -> tuple[str, str]:
    """Path-relative status and stream URLs for *job_id*."""
    return (
        f"{config.api_v1_prefix}/jobs/{job_id}",
        f"{config.api_v1_prefix}/jobs/{job_id}/stream",
    )


def _accepted_position(record: JobRecord) -> int:
    """The 1-based position a just-accepted job holds in its lane."""
    if record.queue_position is None:
        msg = f"accepted job {record.job_id} carries no queue position"
        raise RuntimeError(msg)
    return record.queue_position


def _queue_full_response(exc: QueueFullError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=BaseResponse(success=False, error=str(exc)).model_dump(
            by_alias=True,
            mode="json",
        ),
    )


@router.post("/query", summary="Stream agent query via SSE")
async def stream_query(body: QueryRequest, request: Request) -> StreamingResponse:
    """``POST /api/v1/agent/query``. Streams SSE events.

    Unqueued and deliberately so: a one-shot query holds no branch and no
    worktree, and serializing it behind the workflow lane's concurrency
    of 1 would be a regression.
    """
    await _log.adebug("stream_query_endpoint")
    handler = AgentHandler(
        service=request.app.state.agent_service,
        skills=request.app.state.skills,
    )

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.stream_query(body):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/workflow", summary="Run iterative workflow via SSE")
async def stream_workflow(
    body: WorkflowRequest,
    request: Request,
) -> Response:
    """``POST /api/v1/agent/workflow``. Enqueues, then attaches.

    The leading ``job_accepted`` frame carries the job id so a
    disconnected client reconnects at the stream URL instead of losing
    the run.  Every following frame is what the run emits, unchanged.
    """
    await _log.adebug("stream_workflow_endpoint")
    config: AppConfig = request.app.state.config
    queue: JobQueue = request.app.state.job_queue
    try:
        record: JobRecord = await queue.submit(lane=DEFAULT_LANE, request=body)
    except QueueFullError as exc:
        return _queue_full_response(exc)

    status_url, stream_url = _job_urls(config, record.job_id)
    handler = AgentHandler(
        service=request.app.state.agent_service,
        skills=request.app.state.skills,
        queue=queue,
    )

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.stream_workflow(
            record=record,
            status_url=status_url,
            stream_url=stream_url,
        ):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/fire", status_code=202, summary="Queue a workflow run, no stream")
async def fire_workflow(body: WorkflowRequest, request: Request) -> Response:
    """``POST /api/v1/agent/fire``. Returns the job handle and nothing else."""
    await _log.adebug("fire_workflow_endpoint")
    config: AppConfig = request.app.state.config
    queue: JobQueue = request.app.state.job_queue
    try:
        record: JobRecord = await queue.submit(lane=DEFAULT_LANE, request=body)
    except QueueFullError as exc:
        return _queue_full_response(exc)

    status_url, stream_url = _job_urls(config, record.job_id)
    accepted = FireAcceptedResponse(
        job_id=record.job_id,
        lane=record.lane,
        state=record.state,
        queue_position=_accepted_position(record),
        submitted_at=record.submitted_at,
        status_url=status_url,
        stream_url=stream_url,
    )
    return JSONResponse(
        status_code=202,
        content=accepted.model_dump(by_alias=True, mode="json"),
    )
