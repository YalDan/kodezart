"""The configured Organize owner behind the existing grooming scheduler identity."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from kodezart.core.logging import get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider
from kodezart.domain.errors import OrganizeWriteRefusalError
from kodezart.services.git_observations import read_remote_head
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OrganizeScopeBinding, RepoEntry, RunKind
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity


@dataclass(frozen=True)
class OrganizeTarget:
    binding: OrganizeScopeBinding
    repository: RepoEntry
    owner: OrganizeOwner


class OrganizeTick:
    def __init__(
        self,
        *,
        targets: Sequence[OrganizeTarget],
        git: GitService,
        workspace: WorkspaceProvider,
        remote: str,
    ) -> None:
        self._targets = tuple(targets)
        self._git, self._workspace, self._remote = git, workspace, remote
        self._log = get_logger(__name__)

    async def run(self, started_at: datetime) -> PassRun:
        identity = RunIdentity(
            kind=RunKind.GROOMING,
            name=PromptKey.GROOMING_PASS.value,
            started_at=started_at,
        )
        for target in self._targets:
            async with owned_workspace(
                self._workspace,
                repo_url=target.repository.url,
                ref=target.repository.trunk,
                cache_key=identity.title(),
            ) as workspace:
                head = await read_remote_head(
                    git=self._git,
                    repository=workspace,
                    remote=self._remote,
                    branch=target.repository.trunk,
                )
            if (
                head is None
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) is None
            ):
                raise OrganizeWriteRefusalError(
                    issue_key=target.binding.scope.key,
                    reason="configured repository trunk has no exact remote commit",
                )
            report = await target.owner.run(
                scope=target.binding.scope,
                repo_url=target.repository.url,
                base_ref=head,
                job_id=identity.title(),
                visibility=RepoVisibility.UNKNOWN,
            )
            await self._log.ainfo(
                "organize_scope_finished",
                run_identity=identity.title(),
                scope=target.binding.scope.model_dump(),
                report=report.model_dump(),
            )
            if report.halt is not None:
                raise OrganizeWriteRefusalError(
                    issue_key=target.binding.scope.key,
                    reason=f"Organize halted: {report.halt.cause.value}",
                )
        return PassRun.RAN if self._targets else PassRun.SKIPPED
