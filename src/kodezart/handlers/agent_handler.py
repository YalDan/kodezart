"""Agent handler — unpacks request models, delegates to service."""

import sys
import uuid
from collections.abc import AsyncGenerator

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, WorkflowEngine
from kodezart.core.soft_failure import RaiseSite, SoftFailureError
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import ErrorEvent
from kodezart.types.requests.agent import QueryRequest, WorkflowRequest


def _build_error_event(exc: Exception) -> ErrorEvent:
    """Build a typed ``ErrorEvent`` from an exception.

    Uses explicit ``isinstance`` branches against ``SoftFailureError``
    and ``AgentSDKError`` — NO ``getattr(exc, ...)`` introspection
    (which returns ``Any`` and propagates into Pydantic validation,
    violating ``disallow_any_explicit``).
    """
    cause = exc.__cause__
    error_kind: str = type(exc).__name__
    cause_class: str | None = type(cause).__name__ if cause is not None else None
    stop_reason: str | None = None
    raise_site: RaiseSite | None = None
    rate_limit_rejected: bool | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None

    if isinstance(exc, SoftFailureError):
        raise_site = exc.raise_site
        stop_reason = exc.stop_reason
        rate_limit_rejected = exc.rate_limit_rejected
    elif isinstance(exc, AgentSDKError):
        exit_code = exc.exit_code
        stderr_tail = exc.stderr_tail

    return ErrorEvent(
        error=str(exc),
        error_kind=error_kind,
        cause_class=cause_class,
        stop_reason=stop_reason,
        raise_site=raise_site,
        rate_limit_rejected=rate_limit_rejected,
        exit_code=exit_code,
        stderr_tail=stderr_tail,
    )


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
            yield _build_error_event(exc).model_dump(
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
            cause = exc.__cause__
            await self._log.aexception(
                "stream_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
                cause=type(cause).__name__ if cause is not None else None,
                exc_info=sys.exc_info(),
            )
            yield _build_error_event(exc).model_dump(
                by_alias=True,
                exclude_none=True,
            )
