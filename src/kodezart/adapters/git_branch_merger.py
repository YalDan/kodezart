"""Branch merger — creates feature branch, ff-merges source into it, pushes."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider
from kodezart.types.domain.branch import BackupBranchName


class GitBranchMerger:
    """Fast-forward merges a source branch into a feature branch and pushes.

    Implements the ``BranchMerger`` protocol.
    """

    def __init__(
        self,
        git: GitService,
        workspace: WorkspaceProvider,
    ) -> None:
        self._git = git
        self._workspace = workspace
        self._log: BoundLogger = get_logger(__name__)

    async def merge_and_push(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        feature_branch: str,
        source_branch: str,
        cache_key: str | None = None,
    ) -> str:
        """Fast-forward merge source into feature, push, return SHA."""
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref=base_branch,
            branch_name=feature_branch,
            create_branch=True,
            cache_key=cache_key,
        )
        try:
            await self._git.merge_branch(workspace_path, source_branch)
            await self._git.push(workspace_path, feature_branch)
            return await self._git.current_sha(workspace_path)
        finally:
            await self._workspace.release(workspace_path)

    async def cleanup_source(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        source_branch: str,
        cache_key: str | None = None,
    ) -> None:
        """Delete the source branch from the remote. Must not raise."""
        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
            )
            try:
                await self._git.delete_remote_branch(
                    workspace_path,
                    "origin",
                    source_branch,
                )
            finally:
                await self._workspace.release(workspace_path)
        except Exception as exc:
            await self._log.aerror(
                "branch_cleanup_failed",
                branch=source_branch,
                error=str(exc),
            )

    async def cleanup_backup_branches(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        prefix: str,
        cache_key: str | None = None,
    ) -> None:
        """Batch-delete backup branches. Must not raise."""
        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
            )
            try:
                all_branches = await self._git.list_remote_branches(
                    cwd=workspace_path,
                    remote="origin",
                    prefix=prefix,
                )
                backup_branches = [
                    b for b in all_branches if BackupBranchName.is_backup(b)
                ]
                await self._log.ainfo(
                    "backup_branches_discovered",
                    prefix=prefix,
                    total_matching_prefix=len(all_branches),
                    backup_count=len(backup_branches),
                    branches=backup_branches,
                )
                for branch in backup_branches:
                    await self._git.delete_remote_branch(
                        workspace_path,
                        "origin",
                        branch,
                    )
                    await self._log.ainfo(
                        "backup_branch_deleted",
                        branch=branch,
                    )
            finally:
                await self._workspace.release(workspace_path)
        except Exception as exc:
            await self._log.aerror(
                "backup_cleanup_failed",
                prefix=prefix,
                error=str(exc),
            )
