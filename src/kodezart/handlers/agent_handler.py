"""Agent handler — unpacks request models, delegates to service."""

import uuid
from collections.abc import AsyncGenerator

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, WorkflowEngine
from kodezart.types.domain.agent import ErrorEvent
from kodezart.types.requests.agent import QueryRequest, WorkflowRequest


class AgentHandler:
    """Request handler for agent endpoints.

    Unpacks request models, delegates to ``AgentRunner``/``WorkflowEngine``,
    and serializes events for SSE streaming.
    """

    def __init__(
        self,
        service: AgentRunner,
        workflow_engine: WorkflowEngine | None = None,
    ) -> None:
        self._service = service
        self._workflow_engine = workflow_engine
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
                session_id=request.session_id,
                output_format=output_format,
                cache_key=cache_key,
            ):
                yield event.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            await self._log.aerror("stream_failed", error=str(exc))
            yield ErrorEvent(error=str(exc)).model_dump(
                by_alias=True,
                exclude_none=True,
            )

    async def stream_workflow(
        self,
        request: WorkflowRequest,
    ) -> AsyncGenerator[dict[str, object], None]:
        """Handle ``POST /api/v1/agent/workflow`` by delegating to WorkflowEngine."""
        await self._log.adebug("agent_workflow_requested")
        try:
            if self._workflow_engine is None:
                msg = "Workflow engine not configured"
                raise RuntimeError(msg)
            async for event in self._workflow_engine.run(
                prompt=request.prompt,
                repo_path=request.repo_path,
                repo_url=request.repo_url,
                base_branch=request.base_branch,
                permission_mode=request.permission_mode,
                allowed_tools=request.allowed_tools,
            ):
                yield event.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            await self._log.aerror("stream_failed", error=str(exc))
            yield ErrorEvent(error=str(exc)).model_dump(
                by_alias=True,
                exclude_none=True,
            )
