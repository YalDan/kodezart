"""Job lifecycle endpoints — status and attachable stream."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter
from starlette.responses import JSONResponse, Response, StreamingResponse

from kodezart.api.dependencies import JobHandlerDep, JobRegistryDep, WorkflowHandlerDep
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.types.responses.common import BaseResponse
from kodezart.types.responses.job import JobStatusResponse
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


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": BaseResponse, "description": "Job is unknown"}},
    summary="Job status",
)
async def get_job_status(
    job_id: str, handler: JobHandlerDep
) -> JobStatusResponse | JSONResponse:
    """``GET /api/v1/jobs/{job_id}``. Registry facts plus checkpointed run state."""
    await _log.adebug("job_status_endpoint", job_id=job_id)
    status = await handler.get_status(job_id=job_id)
    if status is None:
        return _not_found(job_id)
    return status


@router.get(
    "/{job_id}/stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        404: {"model": BaseResponse, "description": "Job is unknown"},
    },
    summary="Attach to a job's event stream",
)
async def stream_job(
    job_id: str, registry: JobRegistryDep, handler: WorkflowHandlerDep
) -> Response:
    """``GET /api/v1/jobs/{job_id}/stream``.

    Replays the job's bounded event buffer, then goes live.
    """
    await _log.adebug("stream_job_endpoint", job_id=job_id)
    if await registry.get(job_id=job_id) is None:
        return _not_found(job_id)

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.attach_job(job_id=job_id):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")
