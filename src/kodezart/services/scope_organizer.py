"""Organize one scope against one repository, at that repository's exact head."""

import re

from kodezart.core.logging import get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider
from kodezart.domain.errors import OrganizeWriteRefusalError
from kodezart.services.git_observations import read_remote_head
from kodezart.services.organize_owner import OrganizeOwner
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.organize_owner import OrganizeReport
from kodezart.types.domain.scope import ScopeRef

#: A commit address the configured trunk actually resolves to on the remote.
_EXACT_HEAD = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"


class ScopeOrganizer:
    """Organize one scope against one repository: the exact trunk head, then the owner.

    One unit for the two callers that need it — the scheduled pass over its
    declared bindings, and a scope run's own entry — so the head a phase
    authors against is read the same way on both paths.
    """

    def __init__(
        self,
        *,
        owner: OrganizeOwner,
        git: GitService,
        workspace: WorkspaceProvider,
        remote: str,
    ) -> None:
        self._owner = owner
        self._git, self._workspace, self._remote = git, workspace, remote
        self._log = get_logger(__name__)

    async def run(
        self, *, scope: ScopeRef, repository: RepoEntry, job_id: str
    ) -> OrganizeReport:
        async with owned_workspace(
            self._workspace,
            repo_url=repository.url,
            ref=repository.trunk,
            cache_key=job_id,
        ) as workspace:
            head = await read_remote_head(
                git=self._git,
                repository=workspace,
                remote=self._remote,
                branch=repository.trunk,
            )
        if head is None or re.fullmatch(_EXACT_HEAD, head) is None:
            raise OrganizeWriteRefusalError(
                issue_key=scope.key,
                reason="configured repository trunk has no exact remote commit",
            )
        report = await self._owner.run(
            scope=scope,
            repo_url=repository.url,
            base_ref=head,
            job_id=job_id,
            visibility=RepoVisibility.UNKNOWN,
        )
        await self._log.ainfo(
            "organize_scope_finished",
            run_identity=job_id,
            scope=scope.model_dump(),
            report=report.model_dump(),
        )
        return report
