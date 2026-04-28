"""SSE streaming endpoints for agent execution."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.types.requests.agent import QueryRequest, WorkflowRequest
from kodezart.utils.sse import format_sse

router = APIRouter()
_log: BoundLogger = get_logger(__name__)


@router.post("/query", summary="Stream agent query via SSE")
async def stream_query(body: QueryRequest, request: Request) -> StreamingResponse:
    """``POST /api/v1/agent/query``. Streams SSE events."""
    await _log.adebug("stream_query_endpoint")
    handler = AgentHandler(
        service=request.app.state.agent_service,
        workflow_engine=getattr(
            request.app.state,
            "workflow_engine",
            None,
        ),
    )

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.stream_query(body):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/workflow", summary="Run iterative workflow via SSE")
async def stream_workflow(
    body: WorkflowRequest,
    request: Request,
) -> StreamingResponse:
    """``POST /api/v1/agent/workflow``. Streams SSE events."""
    await _log.adebug("stream_workflow_endpoint")
    handler = AgentHandler(
        service=request.app.state.agent_service,
        workflow_engine=request.app.state.workflow_engine,
    )

    async def generate() -> AsyncGenerator[str, None]:
        async for event in handler.stream_workflow(body):
            yield format_sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")
