"""Owned fresh read-only execution shared by concrete audit consumers."""

import asyncio
import re

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.git_observations import read_workspace_head
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


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
        replacements, cancelled = await finish_owned(
            asyncio.create_task(self._git.has_replace_refs(workspace))
        )
        if cancelled:
            raise asyncio.CancelledError
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
        workspace, cancelled = await finish_owned(
            asyncio.create_task(
                self._workspace.acquire(
                    repo_path=repository, ref=head_sha, create_branch=False
                )
            )
        )
        try:
            if cancelled:
                raise asyncio.CancelledError
            await self._require_head(workspace, head_sha)
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
                    "Audit session produced no structured judgment.",
                    raise_site=site,
                    result_event=result,
                    rate_limit_rejected=rate_limited,
                )
            await self._require_head(workspace, head_sha)
            return result.structured_output
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError
