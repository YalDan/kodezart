"""Branch merger — consolidates a source branch into a feature branch.

Single primitive `consolidate` returns one of four
`ConsolidationStatus` values; never raises on DIVERGENT/SOURCE_MISSING.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, WorkspaceProvider
from kodezart.types.domain.branch import BackupBranchName
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)

_REMOTE = "origin"


class GitBranchMerger:
    """Consolidates a source branch into a feature branch.

    Implements the ``BranchMerger`` protocol.  The four-way decision
    tree of ``consolidate`` is the single source of truth for
    integration semantics; callers route on
    ``ConsolidationOutcome.status`` without ever inspecting refs
    themselves.
    """

    def __init__(
        self,
        git: GitService,
        workspace: WorkspaceProvider,
    ) -> None:
        self._git = git
        self._workspace = workspace
        self._log: BoundLogger = get_logger(__name__)

    async def consolidate(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        feature_branch: str,
        source_branch: str,
        cache_key: str | None = None,
    ) -> ConsolidationOutcome:
        """Total function over the four ConsolidationStatus values.

        Decision tree (verified against git-scm.com docs):
          1. Probe ``source_branch`` on origin via ls-remote.  If absent,
             resolve a feature-tip SHA (origin/feature_branch or
             origin/base_branch) and return SOURCE_MISSING without
             acquiring a worktree.
          2. Acquire a worktree on ``feature_branch`` (creating from
             ``base_branch`` if absent).  ``fetch`` to refresh local
             origin/* refs needed by ``is_ancestor``.
          3. If origin/source is an ancestor of HEAD → ALREADY_INTEGRATED.
          4. If HEAD is an ancestor of origin/source → ff-merge, push,
             delete source from remote → FAST_FORWARDED.
          5. Otherwise → DIVERGENT.
        """
        source_tip = await self._workspace_remote_branch_sha(
            repo_path=repo_path,
            repo_url=repo_url,
            cache_key=cache_key,
            branch=source_branch,
        )
        if source_tip is None:
            feature_tip = await self._resolve_feature_tip_or_raise(
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
                feature_branch=feature_branch,
                base_branch=base_branch,
            )
            await self._log.awarning(
                "consolidate_source_missing",
                source_branch=source_branch,
                feature_branch=feature_branch,
            )
            return ConsolidationOutcome(
                status=ConsolidationStatus.SOURCE_MISSING,
                feature_tip_sha=feature_tip,
            )

        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref=base_branch,
            branch_name=feature_branch,
            create_branch=True,
            cache_key=cache_key,
        )
        try:
            await self._git.fetch(workspace_path)
            origin_source = f"{_REMOTE}/{source_branch}"
            head_sha = await self._git.current_sha(workspace_path)

            if await self._git.is_ancestor(
                workspace_path,
                origin_source,
                "HEAD",
            ):
                return ConsolidationOutcome(
                    status=ConsolidationStatus.ALREADY_INTEGRATED,
                    feature_tip_sha=head_sha,
                )

            if await self._git.is_ancestor(
                workspace_path,
                "HEAD",
                origin_source,
            ):
                await self._git.merge_branch(workspace_path, origin_source)
                await self._git.push(workspace_path, feature_branch)
                new_head = await self._git.current_sha(workspace_path)
                await self._cleanup_source_internal(
                    workspace_path=workspace_path,
                    branch=source_branch,
                )
                return ConsolidationOutcome(
                    status=ConsolidationStatus.FAST_FORWARDED,
                    feature_tip_sha=new_head,
                )

            await self._log.awarning(
                "consolidate_divergent",
                source_branch=source_branch,
                feature_branch=feature_branch,
            )
            return ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT,
                feature_tip_sha=head_sha,
            )
        finally:
            await self._workspace.release(workspace_path)

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
                ref="HEAD",
                cache_key=cache_key,
            )
            try:
                all_branches = await self._git.list_remote_branches(
                    cwd=workspace_path,
                    remote=_REMOTE,
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
                        _REMOTE,
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

    # -- internals -----------------------------------------------------------

    async def _workspace_remote_branch_sha(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str | None,
        branch: str,
    ) -> str | None:
        """Probe a branch on origin without acquiring a feature worktree.

        Uses a transient workspace on HEAD purely as a cwd for the
        ls-remote subprocess call (the GitService API requires a cwd
        even for remote-side queries).
        """
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref="HEAD",
            cache_key=cache_key,
        )
        try:
            return await self._git.remote_branch_sha(
                workspace_path,
                _REMOTE,
                branch,
            )
        finally:
            await self._workspace.release(workspace_path)

    async def _resolve_feature_tip_or_raise(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str | None,
        feature_branch: str,
        base_branch: str,
    ) -> str:
        """Best-effort feature-tip SHA for SOURCE_MISSING outcomes."""
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref="HEAD",
            cache_key=cache_key,
        )
        try:
            feature_tip = await self._git.remote_branch_sha(
                workspace_path,
                _REMOTE,
                feature_branch,
            )
            if feature_tip is not None:
                return feature_tip
            base_tip = await self._git.remote_branch_sha(
                workspace_path,
                _REMOTE,
                base_branch,
            )
            if base_tip is not None:
                return base_tip
            msg = (
                f"SOURCE_MISSING fallback failed: neither "
                f"{feature_branch!r} nor {base_branch!r} present on origin"
            )
            raise RuntimeError(msg)
        finally:
            await self._workspace.release(workspace_path)

    async def _cleanup_source_internal(
        self,
        *,
        workspace_path: str,
        branch: str,
    ) -> None:
        """Delete *branch* from the remote.  Logs but does not raise.

        Invoked only on the FAST_FORWARDED branch of `consolidate`.
        Callers MUST NOT depend on this side effect — it's an internal
        housekeeping action tied to a successful integration.
        """
        try:
            await self._git.delete_remote_branch(
                workspace_path,
                _REMOTE,
                branch,
            )
        except Exception as exc:
            await self._log.aerror(
                "branch_cleanup_failed",
                branch=branch,
                error=str(exc),
            )
