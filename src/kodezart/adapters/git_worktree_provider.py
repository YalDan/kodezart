"""Git worktree workspace provider — local + remote repo support."""

import tempfile
from dataclasses import dataclass

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, RepoCache
from kodezart.domain.agent import generate_job_id
from kodezart.domain.errors import WorkspaceError
from kodezart.types.domain.branch import BackupBranchName


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
        """Remove a tracked worktree and clean up its directory."""
        info = self._workspaces.pop(workspace_path, None)
        if info is None:
            await self._log.awarning(
                "workspace_unknown",
                path=workspace_path,
            )
            return
        await self._backup_if_needed(workspace_path, info)
        await self._git.remove_worktree(info.repo_path, workspace_path)
        await self._log.ainfo("workspace_released", job_id=info.job_id)

    async def _backup_if_needed(
        self,
        workspace_path: str,
        info: _WorkspaceInfo,
    ) -> None:
        """Commit uncommitted changes and push to a backup branch before deletion.

        This lives here (not in a decorator) because _WorkspaceInfo already
        tracks per-workspace state, mypy strict blocks ``**kwargs`` pass-through
        on the 6-param ``acquire()`` signature, and backup on clean worktrees is
        a no-op (one git-status call), so selective wiring adds complexity
        with zero behavioral difference.
        """
        if info.branch_name is None:
            return
        try:
            if await self._git.has_changes(workspace_path):
                await self._git.add_all(workspace_path)
                await self._git.commit(
                    cwd=workspace_path,
                    message="kodezart: emergency backup of in-progress work",
                    author_name=self._committer_name,
                    author_email=self._committer_email,
                )
            backup = BackupBranchName(
                source_branch=info.branch_name,
                job_id_prefix=info.job_id[:8],
            )
            backup_branch = str(backup)
            await self._git.push(workspace_path, backup_branch)
            await self._log.ainfo(
                "workspace_backed_up",
                backup_branch=backup_branch,
                job_id=info.job_id,
            )
        except Exception as exc:
            await self._log.awarning(
                "workspace_backup_failed",
                error=str(exc),
                job_id=info.job_id,
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
