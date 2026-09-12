"""Agent service — orchestrates agent execution as SSE event streams."""

import sys
from collections.abc import AsyncGenerator, Sequence

from pydantic import ValidationError

from kodezart.core.error_egress import build_error_event
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentExecutor,
    ChangePersister,
    NativeWriteGuard,
    WorkspaceProvider,
)
from kodezart.domain.agent import generate_workspace_id
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import WorkspaceError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.types.domain.agent import (
    NATIVE_WRITER_SCHEMA,
    AgentEvent,
    NativeAmendmentEvent,
    ResultEvent,
)
from kodezart.types.domain.amendment import NativeWriterOutput, NativeWriterStart
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import AllowedTools, PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)


class AgentService:
    """Orchestrates agent execution with workspace lifecycle management.

    Implements the ``AgentRunner`` protocol.
    """

    def __init__(
        self,
        executor: AgentExecutor,
        workspace: WorkspaceProvider,
        git_base_url: str,
        persister: ChangePersister | None = None,
    ) -> None:
        self._executor: AgentExecutor = executor
        self._workspace: WorkspaceProvider = workspace
        self._persister: ChangePersister | None = persister
        self._git_base_url: str = git_base_url
        self._log: BoundLogger = get_logger(__name__)

    async def stream(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        branch: str | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a one-shot agent query with automatic workspace acquire/release."""
        if output_format is not None:
            await self._log.adebug(
                "output_format_requested",
                format_type=output_format.get("type"),
            )
        effective_ref = branch or "HEAD"
        async for event in self._run_in_workspace(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            ref=effective_ref,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            skills=skills,
            session_type=session_type,
            run_identity=run_identity,
            agents=agents,
            session_policy=session_policy,
            session_id=session_id,
            output_format=output_format,
            cache_key=cache_key,
        ):
            yield event

    async def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute agent in a pre-acquired workspace (no acquire/release)."""
        async for event in self._executor.stream(
            prompt=prompt,
            cwd=workspace_path,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            skills=skills,
            session_type=session_type,
            run_identity=run_identity,
            agents=agents,
            session_policy=session_policy,
            session_id=session_id,
            output_format=output_format,
        ):
            yield event

    async def stream_workflow(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        base_branch: str = "main",
        branch_name: str | None = None,
        ralph_branch: str | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        visibility: RepoVisibility,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        create_branch: bool = True,
        cache_key: str | None = None,
        native_guard: NativeWriteGuard | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Workflow mode: acquire, execute, persist, release."""
        effective_branch = branch_name or ""
        effective_ralph = ralph_branch or effective_branch
        async for event in self._run_in_workspace(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            ref=base_branch,
            branch_name=effective_ralph,
            create_branch=create_branch,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            skills=skills,
            session_type=session_type,
            run_identity=run_identity,
            agents=agents,
            session_policy=session_policy,
            visibility=visibility,
            persist_branch=effective_ralph,
            cache_key=cache_key,
            native_guard=native_guard,
        ):
            if isinstance(event, ResultEvent):
                event = event.model_copy(
                    update={"branch": effective_branch},
                )
            yield event

    async def _run_in_workspace(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        ref: str,
        branch_name: str | None = None,
        create_branch: bool = True,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
        run_identity: RunIdentity | None = None,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        visibility: RepoVisibility = RepoVisibility.UNKNOWN,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        persist_branch: str | None = None,
        cache_key: str | None = None,
        native_guard: NativeWriteGuard | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if repo_url is not None:
            repo_url = resolve_repo_url(repo_url, self._git_base_url)

        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                ref=ref,
                branch_name=branch_name,
                create_branch=create_branch,
                cache_key=cache_key,
            )
        except WorkspaceError as exc:
            # Log at exception level BEFORE yielding the typed
            # ``ErrorEvent``.  Never silently downgrade a failed
            # workspace acquire to a bare yielded event with no log
            # line — that masks the failure in production observability.
            # ``exc_info=sys.exc_info()`` is passed explicitly to harden
            # against async-executor context loss
            # (hynek/structlog#488 class).
            await self._log.aexception(
                "agent_service_workspace_acquire_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
                exc_info=sys.exc_info(),
            )
            yield build_error_event(exc)
            return

        retain_workspace = False
        try:
            native_start: NativeWriterStart | None = None
            if native_guard is not None:
                if self._persister is None or not persist_branch:
                    raise NativeWriteRefusalError(
                        "Native persistence is not configured"
                    )
                native_start = await native_guard.begin(workspace_path=workspace_path)
                prompt += "\n\n" + native_start.instructions
                output_format = {"type": "json_schema", "schema": NATIVE_WRITER_SCHEMA}
            buffered_result: ResultEvent | None = None
            try:
                async for event in self._executor.stream(
                    prompt=prompt,
                    cwd=workspace_path,
                    permission_mode=permission_mode,
                    allowed_tools=allowed_tools,
                    skills=skills,
                    session_type=session_type,
                    run_identity=run_identity,
                    agents=agents,
                    session_policy=session_policy,
                    session_id=session_id,
                    output_format=output_format,
                ):
                    if isinstance(event, ResultEvent):
                        buffered_result = event
                    else:
                        yield event
            except BaseException:
                if native_guard is not None and native_start is not None:
                    # The executor may fail or be cancelled after moving HEAD.
                    # Read only local identity; a tracker outage cannot erase
                    # this workspace or replace the original writer failure.
                    try:
                        await native_guard.require_unchanged_head(
                            workspace_path=workspace_path,
                            start=native_start,
                        )
                    except BaseException:
                        retain_workspace = True
                        raise
                raise

            before_commit = None
            if native_guard is not None and native_start is not None:
                await native_guard.require_current(
                    workspace_path=workspace_path,
                    start=native_start,
                )
                if (
                    buffered_result is None
                    or buffered_result.is_error
                    or buffered_result.structured_output is None
                ):
                    raise NativeWriteRefusalError(
                        "The native writer returned no claim report"
                    )
                try:
                    output = NativeWriterOutput.model_validate(
                        buffered_result.structured_output
                    )
                except ValidationError as exc:
                    raise NativeWriteRefusalError(
                        "The native writer claim report is malformed"
                    ) from exc
                report = await native_guard.judge(
                    workspace_path=workspace_path,
                    start=native_start,
                    output=output,
                )
                yield NativeAmendmentEvent(report=report)
                if report.upheld:
                    return

                async def before_commit() -> None:
                    await native_guard.require_current(
                        workspace_path=workspace_path,
                        start=native_start,
                    )

            if persist_branch and self._persister and buffered_result:
                backup_ref_id_prefix = (session_id or generate_workspace_id())[:8]
                persist_result = await self._persister.persist(
                    workspace_path=workspace_path,
                    branch=persist_branch,
                    executor=self._executor,
                    backup_ref_id_prefix=backup_ref_id_prefix,
                    skills=skills,
                    visibility=visibility,
                    before_commit=before_commit,
                )
                if persist_result:
                    buffered_result = buffered_result.model_copy(
                        update={
                            "commit_sha": persist_result.commit_sha,
                            "branch": persist_branch,
                        },
                    )

            if buffered_result:
                yield buffered_result
        except NativeWriteRefusalError:
            retain_workspace = True
            raise
        finally:
            if retain_workspace:
                await self._log.awarning(
                    "native_writer_workspace_retained",
                    workspace_path=workspace_path,
                )
            else:
                try:
                    await self._workspace.release(workspace_path)
                except Exception as cleanup_exc:
                    await self._log.awarning(
                        "workspace_cleanup_failed",
                        error=str(cleanup_exc),
                    )
