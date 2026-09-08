"""Bounded fresh verification over caller-owned tracker write actions."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import WriteBackReadError
from kodezart.services.git_observations import read_replace_refs, read_workspace_head
from kodezart.services.tracker_artifacts import (
    read_tracker_artifact,
    require_artifact_read,
)
from kodezart.types.domain.agent import WRITE_BACK_SCHEMA
from kodezart.types.domain.audit import (
    AuditVerdict,
    TrackerArtifact,
    WriteBackJudgment,
    WriteBackRequest,
    WriteBackResult,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


async def _act(action: Callable[[], Awaitable[None]]) -> None:
    async def invoke() -> None:
        await action()

    _, cancelled = await finish_owned(asyncio.create_task(invoke()))
    if cancelled:
        raise asyncio.CancelledError


class TrackerWriteBackVerifier:
    """The caller retains its lease and sanitization around both actions.

    No verified artifact is returned until a fresh session holds and the
    artifact re-read still matches. This does not wire or authorize a writer.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._runner = runner
        self._workspace = workspace
        self._git = git
        self._prompts = prompts
        self._skills = skills
        self._max_rounds = config.write_back_max_verify_rounds

    async def verify(
        self,
        request: WriteBackRequest,
        *,
        write: Callable[[], Awaitable[None]],
        repair: Callable[[TrackerArtifact, WriteBackJudgment], Awaitable[None]],
    ) -> WriteBackResult:
        require_artifact_read(request.surface)
        workspace, cancelled = await finish_owned(
            asyncio.create_task(
                self._workspace.acquire(
                    repo_url=request.repo_url,
                    ref=request.head_sha,
                    create_branch=False,
                    cache_key=request.cache_key,
                )
            )
        )
        try:
            if cancelled:
                raise asyncio.CancelledError
            await self._require_head(workspace, request.head_sha)
            await _act(write)
            rounds: list[WriteBackJudgment] = []
            for _ in range(self._max_rounds):
                artifact = await read_tracker_artifact(
                    tracker=self._tracker, surface=request.surface
                )
                await self._require_head(workspace, request.head_sha)
                judgment = await self._judge(request, artifact, workspace)
                await self._require_head(workspace, request.head_sha)
                current = await read_tracker_artifact(
                    tracker=self._tracker, surface=request.surface
                )
                if current != artifact:
                    raise WriteBackReadError("artifact changed during verification")
                rounds.append(judgment)
                if judgment.verdict is AuditVerdict.HOLDS:
                    return WriteBackResult(
                        verdict=AuditVerdict.HOLDS,
                        rounds=tuple(rounds),
                        verified_artifact=current,
                    )
                if len(rounds) < self._max_rounds:
                    await _act(partial(repair, artifact, judgment))
            return WriteBackResult(
                verdict=AuditVerdict.UNVERIFIABLE,
                rounds=tuple(rounds),
                verified_artifact=None,
            )
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError

    async def _require_head(self, workspace: str, head_sha: str) -> None:
        if await read_replace_refs(git=self._git, workspace=workspace):
            raise WriteBackReadError("verification repository substitutes Git objects")
        observed_head, dirty = await read_workspace_head(
            git=self._git, workspace=workspace
        )
        if observed_head != head_sha:
            raise WriteBackReadError(
                "verification workspace does not match the selected SHA"
            )
        if dirty:
            raise WriteBackReadError("verification workspace has uncommitted changes")

    async def _judge(
        self, request: WriteBackRequest, artifact: TrackerArtifact, workspace: str
    ) -> WriteBackJudgment:
        key = PromptKey.WRITE_BACK_VERIFY
        prompt = self._prompts.template_for(key).render(
            {
                "verification_goal": request.verification_goal,
                "head_sha": request.head_sha,
                "written_artifact": artifact.content,
            }
        )
        result, rate_limited = await drain(
            self._runner.stream_in_workspace(
                prompt=prompt,
                workspace_path=workspace,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=list(EVAL_TOOLS),
                skills=self._prompts.session_skills(key, self._skills),
                session_type=SessionType.SCHEDULED_PASS,
                agents=NO_SUBAGENTS,
                session_policy=self._prompts.session_policy(key),
                session_id=None,
                output_format={"type": "json_schema", "schema": WRITE_BACK_SCHEMA},
            ),
            site="write_back_verify",
        )
        if (
            result is None
            or result.structured_output is None
            or result.is_error
            or rate_limited
        ):
            raise soft_failure(
                "Write-back verifier produced no structured judgment.",
                raise_site="write_back_verify",
                result_event=result,
                rate_limit_rejected=rate_limited,
            )
        return WriteBackJudgment.model_validate(result.structured_output)
