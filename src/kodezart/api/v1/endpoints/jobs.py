"""Job lifecycle endpoints — status and attachable stream."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.handlers.job_handler import JobHandler
from kodezart.types.responses.common import BaseResponse
from kodezart.utils.sse import format_sse

router = APIRouter()
_log: BoundLogger = get_logger(__name__)


def _not_found(job_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=BaseResponse(
            success=False,
            error=f"job not found: {job_id}",
        ).model_dump(by_alias=True, mode="json"),
    )


@router.get("/{job_id}", summary="Job status")
async def get_job_status(job_id: str, request: Request) -> Response:
    """``GET /api/v1/jobs/{job_id}``. Registry facts plus checkpointed run state."""
    await _log.adebug("job_status_endpoint", job_id=job_id)
    handler = JobHandler(service=request.app.state.job_service)
    status = await handler.get_status(job_id=job_id)
    if status is None:
        return _not_found(job_id)
    return JSONResponse(
        status_code=200,
        content=status.model_dump(by_alias=True, mode="json"),
    )


@router.get("/{job_id}/stream", summary="Attach to a job's event stream")
async def stream_job(job_id: str, request: Request) -> Response:
    """``GET /api/v1/jobs/{job_id}/stream``.

    Replays the job's bounded event buffer, then goes live.
    """
    await _log.adebug("stream_job_endpoint", job_id=job_id)
    registry: JobRegistry = request.app.state.job_queue
    if await registry.get(job_id=job_id) is None:
        return _not_found(job_id)

    queue: JobQueue = request.app.state.job_queue
    handler = AgentHandler(service=request.app.state.agent_service, queue=queue)

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.attach_job(job_id=job_id):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")
