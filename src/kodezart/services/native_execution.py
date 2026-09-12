"""Existing native AgentService effects expressed as resumable graph phases."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from langgraph.config import get_stream_writer
from pydantic import ValidationError

from kodezart.chains.native_execution import NativeExecutionGraph, NativeExecutionState
from kodezart.core.logging import get_logger
from kodezart.core.protocols import (
    AgentExecutor,
    ChangePersister,
    NativeWriteGuard,
    WorkspaceProvider,
)
from kodezart.domain.agent import generate_workspace_id
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import WorkspaceError
from kodezart.types.domain.agent import (
    NATIVE_WRITER_SCHEMA,
    AgentEvent,
    NativeAmendmentEvent,
    ResultEvent,
)
from kodezart.types.domain.amendment import NativeWriterOutput
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.native_execution import (
    ActiveNativeExecution,
    NewNativeExecution,
    PersistedNativeExecution,
    PreparedNativeExecution,
    ReconciledNativeExecution,
    RefusedNativeExecution,
    UnchangedNativeExecution,
    WrittenNativeExecution,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import AllowedTools, PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import AgentDefinition, SessionPolicy


@dataclass(frozen=True, slots=True)
class NativeExecutionRequest:
    """Actual call arguments; ports and callbacks never enter checkpoint state."""

    prompt: str
    repo_path: str | None
    repo_url: str | None
    ref: str
    branch: str
    create_branch: bool
    permission_mode: PermissionMode
    allowed_tools: AllowedTools
    skills: SkillsSelection
    session_type: SessionType
    run_identity: RunIdentity | None
    agents: Sequence[AgentDefinition]
    session_policy: SessionPolicy
    session_id: str | None
    visibility: RepoVisibility
    cache_key: str | None


class NativeExecution:
    """Own one native service invocation using its original collaborators."""

    def __init__(
        self,
        *,
        executor: AgentExecutor,
        workspace: WorkspaceProvider,
        persister: ChangePersister,
        guard: NativeWriteGuard,
        request: NativeExecutionRequest,
    ) -> None:
        self._executor, self._workspace = executor, workspace
        self._persister, self._guard, self._request = persister, guard, request
        self._active_workspace: str | None = None
        self._release_incomplete_workspace = False
        self._writer_failure: BaseException | None = None
        self._log = get_logger(__name__)

    async def _restore(self, phase: ActiveNativeExecution) -> None:
        request = self._request
        if (
            phase.cache_key != request.cache_key
            or phase.run_identity != request.run_identity
            or phase.workspace.identity.branch != request.branch
            or phase.authority.holder != self._guard.holder
        ):
            raise NativeWriteRefusalError(
                "The saved writer belongs to another native invocation"
            )
        await self._resume_workspace(phase)
        self._active_workspace = phase.workspace.workspace_path
        await self._guard.restore(
            snapshot=phase.authority,
            workspace_path=phase.workspace.workspace_path,
            start=phase.start,
            receipt=phase.receipt
            if isinstance(phase, PersistedNativeExecution)
            else None,
        )
        # Tracker validation awaits external reads. Recheck the original files
        # and index after those reads before the next native effect starts.
        await self._resume_workspace(phase)

    async def _resume_workspace(self, phase: ActiveNativeExecution) -> None:
        try:
            await self._workspace.resume(
                snapshot=phase.workspace,
                holder=self._guard.holder,
                repo_path=self._request.repo_path,
                repo_url=self._request.repo_url,
                cache_key=self._request.cache_key,
            )
        except WorkspaceError as exc:
            raise NativeWriteRefusalError(
                "The original native workspace cannot be resumed"
            ) from exc

    async def prepare(self) -> PreparedNativeExecution:
        """Acquire once and checkpoint the actual original native authority."""
        request = self._request
        path = await self._workspace.acquire(
            repo_path=request.repo_path,
            repo_url=request.repo_url,
            ref=request.ref,
            branch_name=request.branch,
            create_branch=request.create_branch,
            cache_key=request.cache_key,
        )
        self._active_workspace = path
        start = await self._guard.begin(workspace_path=path)
        return PreparedNativeExecution(
            workspace=await self._workspace.capture(
                workspace_path=path, holder=self._guard.holder
            ),
            start=start,
            authority=self._guard.snapshot(),
            cache_key=request.cache_key,
            run_identity=request.run_identity,
        )

    async def write(self, phase: PreparedNativeExecution) -> WrittenNativeExecution:
        """Only an uncompleted writer opens a new SDK session."""
        await self._restore(phase)
        request, path = self._request, phase.workspace.workspace_path
        result: ResultEvent | None = None
        writer = get_stream_writer()
        try:
            async for event in self._executor.stream(
                prompt=request.prompt + "\n\n" + phase.start.instructions,
                cwd=path,
                permission_mode=request.permission_mode,
                allowed_tools=request.allowed_tools,
                skills=request.skills,
                session_type=request.session_type,
                run_identity=request.run_identity,
                agents=request.agents,
                session_policy=request.session_policy,
                session_id=request.session_id,
                output_format={"type": "json_schema", "schema": NATIVE_WRITER_SCHEMA},
            ):
                if isinstance(event, ResultEvent):
                    result = event
                else:
                    writer(event)
        except BaseException as failure:
            self._writer_failure = failure
            # Preserve the original incomplete-writer cleanup contract. A saved
            # Prepared phase then refuses its missing workspace on resume; it
            # never silently opens a replacement writer. Completed phases and
            # moved or uncertain HEAD always retain their evidence.
            try:
                await self._guard.require_unchanged_head(
                    workspace_path=path, start=phase.start
                )
            except BaseException:
                # A cleanup read must not replace the writer's failure.
                self._release_incomplete_workspace = False
            else:
                self._release_incomplete_workspace = True
            raise
        await self._guard.require_current(workspace_path=path, start=phase.start)
        if result is None or result.is_error or result.structured_output is None:
            raise NativeWriteRefusalError("The native writer returned no claim report")
        try:
            output = NativeWriterOutput.model_validate(result.structured_output)
        except ValidationError as exc:
            raise NativeWriteRefusalError(
                "The native writer claim report is malformed"
            ) from exc
        return WrittenNativeExecution.model_validate(
            {
                **phase.model_dump(exclude={"phase"}),
                "result": result,
                "output": output,
                "workspace": await self._workspace.capture(
                    workspace_path=path, holder=self._guard.holder
                ),
                "authority": self._guard.snapshot(),
            }
        )

    async def reconcile(
        self, phase: WrittenNativeExecution
    ) -> ReconciledNativeExecution | RefusedNativeExecution:
        """Resume the same claim graph only under its original source authority."""
        await self._restore(phase)
        path = phase.workspace.workspace_path
        report = await self._guard.judge(
            workspace_path=path,
            start=phase.start,
            output=phase.output,
        )
        # Reconciliation changes tracker records, never the completed writer's
        # index or working content. Validate that independent identity again.
        await self._resume_workspace(phase)
        payload = {
            **phase.model_dump(exclude={"phase"}),
            "report": report,
            "authority": self._guard.snapshot(),
        }
        completed = (
            RefusedNativeExecution.model_validate(payload)
            if report.upheld
            else ReconciledNativeExecution.model_validate(payload)
        )
        get_stream_writer()(NativeAmendmentEvent(report=report))
        return completed

    async def persist(
        self, phase: ReconciledNativeExecution
    ) -> PersistedNativeExecution | UnchangedNativeExecution:
        """Persist only after restoring the actual completed reconciliation."""
        await self._restore(phase)
        path, request = phase.workspace.workspace_path, self._request

        async def before_commit() -> None:
            await self._guard.require_current(workspace_path=path, start=phase.start)

        async def before_publish(sha: str) -> None:
            await self._guard.require_publishable(
                workspace_path=path,
                start=phase.start,
                authorized_commit_sha=sha,
            )

        receipt = await self._persister.persist(
            workspace_path=path,
            branch=request.branch,
            executor=self._executor,
            backup_ref_id_prefix=(request.session_id or generate_workspace_id())[:8],
            skills=request.skills,
            visibility=request.visibility,
            before_commit=before_commit,
            before_publish=before_publish,
        )
        payload = {
            **phase.model_dump(exclude={"phase"}),
            "workspace": await self._workspace.capture(
                workspace_path=path, holder=self._guard.holder
            ),
            "authority": self._guard.snapshot(),
        }
        if receipt is None:
            return UnchangedNativeExecution.model_validate(payload)
        return PersistedNativeExecution.model_validate({**payload, "receipt": receipt})

    async def stream(self) -> AsyncIterator[AgentEvent]:
        """Stream existing events and retain interrupted native workspace evidence."""
        completed = False
        report_emitted = False
        state = NativeExecutionState(execution=NewNativeExecution())
        graph = NativeExecutionGraph(actions=self).graph
        try:
            async for mode, value in graph.astream(
                state,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    if not isinstance(value, AgentEvent):
                        raise TypeError(
                            "Native phase stream requires an actual AgentEvent"
                        )
                    report_emitted = report_emitted or isinstance(
                        value, NativeAmendmentEvent
                    )
                    yield value
                else:
                    state = NativeExecutionState.model_validate(value)
            # The installed framework can finish a stream after a child task
            # cancels itself. Preserve the actual writer failure in this runtime
            # invocation; exceptions are never serialized as phase state.
            if self._writer_failure is not None:
                raise self._writer_failure
            phase = state.execution
            if not isinstance(
                phase,
                (
                    PersistedNativeExecution,
                    UnchangedNativeExecution,
                    RefusedNativeExecution,
                ),
            ):
                raise NativeWriteRefusalError(
                    "Native execution stopped before a completed phase"
                )
            await self._restore(phase)
            if not report_emitted:
                yield NativeAmendmentEvent(report=phase.report)
            if not isinstance(phase, RefusedNativeExecution):
                if isinstance(phase, PersistedNativeExecution):
                    yield ResultEvent.model_validate(
                        {
                            **phase.result.model_dump(),
                            "commit_sha": phase.receipt.commit_sha,
                            "branch": phase.receipt.branch,
                        }
                    )
                else:
                    yield phase.result
            completed = True
        finally:
            path = self._active_workspace
            if path is not None:
                if completed or self._release_incomplete_workspace:
                    try:
                        await self._workspace.release(path)
                    except Exception as cleanup_exc:
                        await self._log.awarning(
                            "workspace_cleanup_failed", error=str(cleanup_exc)
                        )
                else:
                    await self._log.awarning(
                        "native_writer_workspace_retained", workspace_path=path
                    )
