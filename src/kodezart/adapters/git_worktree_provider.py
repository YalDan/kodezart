"""Git worktree workspace provider — local + remote repo support.

Recovery-via-backup-branch is intentionally disabled; re-introducing it
must be via the typed state contract (a ``WorkflowState`` field plus an
SSE event) — see the consolidation plan's out-of-scope note.  Until
then, a dirty release surfaces as a structured ``workspace_release_unclean``
warning rather than a silent ``-backup-<hex>`` push.
"""

import tempfile
from dataclasses import dataclass

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, RepoCache
from kodezart.domain.agent import generate_job_id
from kodezart.domain.errors import WorkspaceError


@dataclass(frozen=True, slots=True)
class _WorkspaceInfo:
    repo_path: str
    job_id: str
    branch_name: str | None = None


def _worktree_path(job_id: str) -> str:
    return f"{tempfile.gettempdir()}/kodezart-{job_id}"


class GitWorktreeProvider:
    """Disposable Git worktrees in ``/tmp`` for agent execution.

    Implements the ``WorkspaceProvider`` protocol.

    Recovery-via-backup-branch is intentionally disabled; re-introducing
    it must be via the typed state contract — see the consolidation
    plan's out-of-scope note.  Dirty release emits a structured warning
    and removes the worktree; it does NOT push to any backup ref.
    """

    def __init__(
        self,
        git: GitService,
        cache: RepoCache,
        committer_name: str,
        committer_email: str,
    ) -> None:
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._committer_name: str = committer_name
        self._committer_email: str = committer_email
        self._workspaces: dict[str, _WorkspaceInfo] = {}
        self._log: BoundLogger = get_logger(__name__)

    async def acquire(
        self,
        *,
        repo_path: str | None = None,
        repo_url: str | None = None,
        ref: str,
        branch_name: str | None = None,
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> str:
        """Resolve repo, create worktree, return its path."""
        try:
            resolved = await self._resolve(
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
            )
            await self._git.validate_repo(resolved)

            job_id = generate_job_id()
            wt_path = _worktree_path(job_id)
            await self._git.create_worktree(
                resolved,
                ref,
                wt_path,
                branch_name,
                create_branch=create_branch,
            )

            self._workspaces[wt_path] = _WorkspaceInfo(
                repo_path=resolved,
                job_id=job_id,
                branch_name=branch_name,
            )
            await self._log.ainfo(
                "workspace_acquired",
                job_id=job_id,
                path=wt_path,
            )
            return wt_path
        except WorkspaceError:
            raise
        except (ValueError, RuntimeError) as exc:
            raise WorkspaceError(str(exc)) from exc

    async def release(self, workspace_path: str) -> None:
        """Remove a tracked worktree and clean up its directory.

        Dirty release emits a ``workspace_release_unclean`` warning but
        does NOT push to any backup branch.  Persister failures upstream
        should surface as their own loud errors rather than be masked
        here.
        """
        info = self._workspaces.pop(workspace_path, None)
        if info is None:
            await self._log.awarning(
                "workspace_unknown",
                path=workspace_path,
            )
            return
        await self._log_release_state(workspace_path, info)
        await self._git.remove_worktree(info.repo_path, workspace_path)
        await self._log.ainfo("workspace_released", job_id=info.job_id)

    async def _log_release_state(
        self,
        workspace_path: str,
        info: _WorkspaceInfo,
    ) -> None:
        """Emit a structured warning when releasing a dirty workspace.

        No backup-branch push, no commit, no recovery.  Dirty state at
        release is a programming error elsewhere; surfacing it loudly is
        the new contract.
        """
        # TODO(resume): re-entering a checkpointed run from this worktree is
        # not yet wired — see the time-travel TODOs in ralph_loop / ralph_workflow.
        try:
            dirty = await self._git.has_changes(workspace_path)
        except Exception as exc:
            await self._log.awarning(
                "workspace_release_status_unknown",
                error=str(exc),
                job_id=info.job_id,
            )
            return
        if dirty:
            await self._log.awarning(
                "workspace_release_unclean",
                job_id=info.job_id,
                branch_name=info.branch_name,
                path=workspace_path,
            )

    async def _resolve(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str | None,
    ) -> str:
        if repo_url is not None:
            return await self._cache.ensure_available(repo_url, cache_key)
        if repo_path is not None:
            return repo_path
        msg = "No repository specified"
        raise WorkspaceError(msg)
