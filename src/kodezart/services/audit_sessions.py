"""Owned fresh read-only execution shared by concrete audit consumers."""

import re

from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import soft_failure
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.git_observations import read_workspace_head
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


async def judge_in_workspace(
    *,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    workspace: str,
    key: PromptKey,
    prompt: str,
    output_schema: dict[str, object],
    site: RaiseSite,
    session_type: SessionType,
    failure_message: str,
) -> dict[str, object]:
    """Run a fresh read-only judgment inside the caller's owned workspace.

    The caller retains workspace/source validation and output interpretation.
    Session type and error context belong to that caller's phase.
    """
    result, rate_limited = await drain(
        runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=workspace,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=ToolPreset.EVALUATION,
            skills=prompts.session_skills(key, skills),
            session_type=session_type,
            agents=NO_SUBAGENTS,
            session_policy=prompts.session_policy(key),
            session_id=None,
            output_format={"type": "json_schema", "schema": output_schema},
        ),
        site=site,
    )
    if (
        result is None
        or result.structured_output is None
        or result.is_error
        or rate_limited
    ):
        raise soft_failure(
            failure_message,
            raise_site=site,
            result_event=result,
            rate_limit_rejected=rate_limited,
        )
    return result.structured_output


class FreshAuditSession:
    """Own a clean immutable workspace without any prior-session input."""

    def __init__(
        self,
        *,
        git: GitService,
        workspace: WorkspaceProvider,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._git = git
        self._workspace = workspace
        self._runner = runner
        self._prompts = prompts
        self._skills = skills

    async def _require_head(self, workspace: str, head_sha: str) -> None:
        replacements = await settle(self._git.has_replace_refs(workspace))
        if replacements:
            raise AuditClaimReadError("the audit repository substitutes Git objects")
        if await read_workspace_head(git=self._git, workspace=workspace) != (
            head_sha,
            False,
        ):
            raise AuditClaimReadError("the audit workspace is not clean at its head")

    async def judge(
        self,
        *,
        repository: str,
        head_sha: str,
        key: PromptKey,
        prompt: str,
        output_schema: dict[str, object],
        site: RaiseSite,
    ) -> dict[str, object]:
        """Return structured output after settled execution and workspace checks.

        Source collection and result validation belong to the caller. This
        helper neither reads stored judgments nor applies the returned one.
        """
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head_sha) is None:
            raise AuditClaimReadError("an audit session needs a complete commit SHA")
        async with owned_workspace(
            self._workspace, repo_path=repository, ref=head_sha
        ) as workspace:
            await self._require_head(workspace, head_sha)
            structured = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=workspace,
                key=key,
                prompt=prompt,
                output_schema=output_schema,
                site=site,
                session_type=SessionType.SCHEDULED_PASS,
                failure_message="Audit session produced no structured judgment.",
            )
            await self._require_head(workspace, head_sha)
            return structured
