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
        *,
        remote: str,
    ) -> None:
        self._git = git
        self._workspace = workspace
        self._remote = remote
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
          2. Acquire a worktree on ``feature_branch``, creating it if absent
             from the commit the source split from ``base_branch`` (their
             merge base), not from ``base_branch`` itself: a trunk that moved
             after the source was cut would otherwise leave the new feature
             branch and the source each holding commits the other lacks.
             ``fetch`` to refresh local origin/* refs needed by
             ``is_ancestor``.
          3. If origin/source is an ancestor of HEAD → ALREADY_INTEGRATED.
          4. If HEAD is an ancestor of origin/source → ff-merge, push,
             delete source from remote when its tip still equals the
             commit that was integrated → FAST_FORWARDED.
          5. Otherwise → DIVERGENT.
        """
        cut_point = await self._source_cut_point(
            repo_path=repo_path,
            repo_url=repo_url,
            cache_key=cache_key,
            base_branch=base_branch,
            source_branch=source_branch,
        )
        if cut_point is None:
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
            ref=cut_point,
            branch_name=feature_branch,
            create_branch=True,
            cache_key=cache_key,
        )
        try:
            await self._git.fetch(workspace_path)
            origin_source = f"{self._remote}/{source_branch}"
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
                # A fast-forward moves HEAD onto the source tip, so the
                # post-merge HEAD IS the commit that was integrated.  It is
                # threaded rather than re-derived: a second probe of the
                # remote would be a different observation, which is exactly
                # the race the guard exists to close.
                await self._cleanup_source_internal(
                    workspace_path=workspace_path,
                    branch=source_branch,
                    merged_sha=new_head,
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
                    remote=self._remote,
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
                        self._remote,
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

    async def _source_cut_point(
        self,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str | None,
        base_branch: str,
        source_branch: str,
    ) -> str | None:
        """Where a new feature branch starts, or ``None`` when the source is absent.

        Probes the source on origin without acquiring a feature worktree,
        from a transient workspace on HEAD (the GitService API requires a
        cwd even for remote-side queries). When the source exists, the
        answer is the merge base of ``base_branch`` and the fetched source:
        the commit the source was cut from, however far the trunk has moved
        since. With no merge base, ``base_branch`` itself.
        """
        workspace_path = await self._workspace.acquire(
            repo_path=repo_path,
            repo_url=repo_url,
            ref="HEAD",
            cache_key=cache_key,
        )
        try:
            source_tip = await self._git.remote_branch_sha(
                workspace_path,
                self._remote,
                source_branch,
            )
            if source_tip is None:
                return None
            await self._git.fetch(workspace_path)
            split = await self._git.merge_base(
                workspace_path,
                base_branch,
                f"{self._remote}/{source_branch}",
            )
            return base_branch if split is None else split
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
                self._remote,
                feature_branch,
            )
            if feature_tip is not None:
                return feature_tip
            base_tip = await self._git.remote_branch_sha(
                workspace_path,
                self._remote,
                base_branch,
            )
            if base_tip is not None:
                return base_tip
            msg = (
                f"SOURCE_MISSING fallback failed: neither "
                f"{feature_branch!r} nor {base_branch!r} present on {self._remote}"
            )
            raise RuntimeError(msg)
        finally:
            await self._workspace.release(workspace_path)

    async def _cleanup_source_internal(
        self,
        *,
        workspace_path: str,
        branch: str,
        merged_sha: str,
    ) -> None:
        """Delete *branch* from the remote.  Logs but does not raise.

        Invoked only on the FAST_FORWARDED branch of `consolidate`.
        Callers MUST NOT depend on this side effect — it's an internal
        housekeeping action tied to a successful integration.

        The merge integrated the locally fetched ``<remote>/<branch>``, so
        ``merged_sha`` — the post-merge HEAD of a fast-forward — is the
        source tip that was actually integrated.  The delete fires only
        when the remote still points at exactly that commit.  A source
        branch that advanced between the ``fetch`` and here carries commits
        the feature branch does not have, and deleting it would destroy
        work that was pushed while this consolidation was in flight.

        The re-probe narrows the staleness window to a single round-trip;
        it does not close it, so the try/except stays.
        """
        try:
            remote_sha = await self._git.remote_branch_sha(
                workspace_path,
                self._remote,
                branch,
            )
            if remote_sha is None:
                await self._log.adebug(
                    "branch_cleanup_skipped",
                    branch=branch,
                )
                return
            if remote_sha != merged_sha:
                await self._log.awarning(
                    "branch_cleanup_source_advanced",
                    branch=branch,
                    merged_sha=merged_sha,
                    remote_sha=remote_sha,
                )
                return
            await self._git.delete_remote_branch(
                workspace_path,
                self._remote,
                branch,
            )
        except Exception as exc:
            await self._log.aerror(
                "branch_cleanup_failed",
                branch=branch,
                error=str(exc),
            )
