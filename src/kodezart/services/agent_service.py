"""Agent service — orchestrates agent execution as SSE event streams."""

from collections.abc import AsyncGenerator

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, ChangePersister, WorkspaceProvider
from kodezart.domain.errors import WorkspaceError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.types.domain.agent import AgentEvent, ErrorEvent, ResultEvent


class AgentService:
    """Orchestrates agent execution with workspace lifecycle management.

    Implements the ``AgentRunner`` protocol.
    """

    def __init__(
        self,
        executor: AgentExecutor,
        workspace: WorkspaceProvider,
        persister: ChangePersister | None = None,
        git_base_url: str = "https://github.com",
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
        permission_mode: str,
        allowed_tools: list[str],
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
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute agent in a pre-acquired workspace (no acquire/release)."""
        async for event in self._executor.stream(
            prompt=prompt,
            cwd=workspace_path,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
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
        permission_mode: str,
        allowed_tools: list[str],
        create_branch: bool = True,
        cache_key: str | None = None,
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
            persist_branch=effective_ralph,
            cache_key=cache_key,
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
        permission_mode: str,
        allowed_tools: list[str],
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        persist_branch: str | None = None,
        cache_key: str | None = None,
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
            yield ErrorEvent(error=str(exc))
            return

        try:
            buffered_result: ResultEvent | None = None
            async for event in self._executor.stream(
                prompt=prompt,
                cwd=workspace_path,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                session_id=session_id,
                output_format=output_format,
            ):
                if isinstance(event, ResultEvent):
                    buffered_result = event
                else:
                    yield event

            if persist_branch and self._persister and buffered_result:
                persist_result = await self._persister.persist(
                    workspace_path=workspace_path,
                    branch=persist_branch,
                    executor=self._executor,
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
        finally:
            try:
                await self._workspace.release(workspace_path)
            except Exception as cleanup_exc:
                await self._log.awarning(
                    "workspace_cleanup_failed",
                    error=str(cleanup_exc),
                )
