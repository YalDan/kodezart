"""Git worktree workspace provider — local + remote repo support.

Recovery-via-backup-branch is intentionally disabled; re-introducing it
must be via the typed state contract (a ``WorkflowState`` field plus an
SSE event) — see the consolidation plan's out-of-scope note.  Until
then, a dirty release surfaces as a structured ``workspace_release_unclean``
warning rather than a silent ``-backup-<hex>`` push.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.prompt_namespaces import repo_display
from kodezart.core.protocols import GitService, RepoCache
from kodezart.domain.agent import generate_workspace_id
from kodezart.domain.errors import GitOperationError, GitRepositoryError, WorkspaceError
from kodezart.types.domain.workspace import WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class _WorkspaceInfo:
    repo_path: str
    workspace_id: str
    branch_name: str | None = None


def _worktree_path(workspace_id: str) -> str:
    return f"{tempfile.gettempdir()}/kodezart-{workspace_id}"


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
    ) -> None:
        self._git: GitService = git
        self._cache: RepoCache = cache
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
        parent: str | None = None,
    ) -> str:
        """Resolve repo, create worktree, return its path.

        Given *parent*, the worktree is ``{parent}/{name}``, where the name is
        the one the prompts call the repository by.
        """
        try:
            resolved = await self._resolve(
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
            )
            await self._git.validate_repo(resolved)

            workspace_id = generate_workspace_id()
            wt_path = (
                _worktree_path(workspace_id)
                if parent is None
                else str(Path(parent) / repo_display(repo_url or resolved)[0])
            )
            await self._git.create_worktree(
                resolved,
                ref,
                wt_path,
                branch_name,
                create_branch=create_branch,
            )

            self._workspaces[wt_path] = _WorkspaceInfo(
                repo_path=resolved,
                workspace_id=workspace_id,
                branch_name=branch_name,
            )
            await self._log.ainfo(
                "workspace_acquired",
                workspace_id=workspace_id,
                path=wt_path,
            )
            return wt_path
        except WorkspaceError:
            raise
        except (GitOperationError, GitRepositoryError) as exc:
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
        await self._log.ainfo("workspace_released", workspace_id=info.workspace_id)

    async def capture(self, *, workspace_path: str, holder: str) -> WorkspaceSnapshot:
        """Capture an actual acquisition and fingerprints of its current contents."""
        info = self._workspaces.get(workspace_path)
        if info is None or info.branch_name is None or not holder.strip():
            raise WorkspaceError(
                "A native checkpoint requires an owned branch workspace"
            )
        identity = await self._git.worktree_identity(
            workspace_path, repository_path=info.repo_path
        )
        if identity.branch != info.branch_name:
            raise WorkspaceError("The owned workspace changed its native branch")
        repository = Path(info.repo_path).resolve(strict=True)
        observed = repository.stat()
        return WorkspaceSnapshot(
            workspace_path=workspace_path,
            workspace_id=info.workspace_id,
            repository_path=str(repository),
            repository_device=observed.st_dev,
            repository_inode=observed.st_ino,
            holder=holder,
            identity=identity,
        )

    async def resume(
        self,
        *,
        snapshot: WorkspaceSnapshot,
        holder: str,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str | None,
    ) -> None:
        """Validate retained Git/filesystem facts before adopting its original lease."""
        if holder != snapshot.holder or not holder.strip():
            raise WorkspaceError("The retained workspace belongs to another native job")
        resolved = await self._resolve(
            repo_path=repo_path, repo_url=repo_url, cache_key=cache_key
        )
        try:
            requested = Path(resolved).resolve(strict=True)
            repository = Path(snapshot.repository_path).resolve(strict=True)
            observed = repository.stat()
            if (
                requested != repository
                or str(repository) != snapshot.repository_path
                or (
                    observed.st_dev,
                    observed.st_ino,
                )
                != (snapshot.repository_device, snapshot.repository_inode)
            ):
                raise WorkspaceError("The acquired repository was replaced")
        except OSError as exc:
            raise WorkspaceError("The retained workspace is unavailable") from exc
        try:
            await self._git.validate_repo(str(repository))
        except (GitRepositoryError, GitOperationError) as exc:
            raise WorkspaceError("The retained repository is unavailable") from exc
        identity = await self._git.worktree_identity(
            snapshot.workspace_path, repository_path=str(repository)
        )
        if identity != snapshot.identity:
            raise WorkspaceError("The retained workspace or its content changed")
        info = _WorkspaceInfo(
            repo_path=snapshot.repository_path,
            workspace_id=snapshot.workspace_id,
            branch_name=identity.branch,
        )
        existing = self._workspaces.get(snapshot.workspace_path)
        if existing is not None and existing != info:
            # Relative acquisition paths and resolved ones may differ only in
            # spelling; compare the actual original repository in that case.
            if (
                Path(existing.repo_path).resolve() != repository
                or existing.workspace_id != info.workspace_id
                or existing.branch_name != info.branch_name
            ):
                raise WorkspaceError("Another acquisition already owns this workspace")
        self._workspaces[snapshot.workspace_path] = info

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
        try:
            dirty = await self._git.has_changes(workspace_path)
        except Exception as exc:
            await self._log.awarning(
                "workspace_release_status_unknown",
                error=str(exc),
                workspace_id=info.workspace_id,
            )
            return
        if dirty:
            await self._log.awarning(
                "workspace_release_unclean",
                workspace_id=info.workspace_id,
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
