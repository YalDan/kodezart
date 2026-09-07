"""Compose and check a scope's pinned lane heads in one disposable tree."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from kodezart.core.protocols import CheckChainRunner, GitService
from kodezart.domain.errors import CheckChainExecutionError
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.union import (
    UnionCompositionResult,
    UnionLaneHead,
    UnionScratchObservation,
)


async def _finish_owned[T](task: asyncio.Task[T]) -> tuple[T, bool]:
    """Settle a Git operation before removing the tree it may still use."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


class UnionComposition:
    """A scope-level consumer of GitService and CheckChainRunner.

    The caller supplies the planner's ordered current-head snapshot and a
    selected immutable base. No forge writer or branch publisher is held.
    """

    def __init__(
        self,
        *,
        git: GitService,
        runner: CheckChainRunner,
        author_name: str,
        author_email: str,
    ) -> None:
        self._git = git
        self._runner = runner
        self._author_name = author_name
        self._author_email = author_email

    async def verify(
        self,
        *,
        scope_key: str,
        repo_path: str,
        repo: RepoEntry,
        base_sha: str,
        lane_heads: Sequence[UnionLaneHead],
    ) -> UnionCompositionResult:
        """Return the measured scratch result; release its tree on every exit."""
        with TemporaryDirectory(prefix="kodezart-union-") as directory:
            worktree = str(Path(directory) / "tree")
            snapshot = UnionScratchObservation(
                scope_key=scope_key,
                repository_url=repo.url,
                base_sha=base_sha,
                lane_heads=tuple(lane_heads),
                scratch_path=worktree,
                scratch_sha=base_sha,
            )
            created = False
            try:
                _, cancelled = await _finish_owned(
                    asyncio.create_task(
                        self._git.create_worktree(
                            repo_path,
                            base_sha,
                            worktree,
                            branch_name=None,
                            create_branch=False,
                        )
                    )
                )
                created = True
                if cancelled:
                    raise asyncio.CancelledError
                for head in snapshot.lane_heads:
                    _, cancelled = await _finish_owned(
                        asyncio.create_task(
                            self._git.merge_scratch_head(
                                cwd=worktree,
                                head_sha=head.head_sha,
                                author_name=self._author_name,
                                author_email=self._author_email,
                            )
                        )
                    )
                    if cancelled:
                        raise asyncio.CancelledError
                if not repo.checks:
                    raise CheckChainExecutionError(
                        cwd=worktree,
                        step_name=None,
                        reason="no check chain is declared",
                    )
                checks = await self._runner.run_chain(cwd=worktree, steps=repo.checks)
                scratch_sha = await self._git.current_sha(worktree)
                return UnionCompositionResult(
                    **snapshot.model_dump(exclude={"scratch_sha"}),
                    scratch_sha=scratch_sha,
                    checks=checks,
                )
            finally:
                if created or self._git.is_repo(worktree):
                    _, cancelled = await _finish_owned(
                        asyncio.create_task(
                            self._git.remove_worktree(repo_path, worktree)
                        )
                    )
                    if cancelled:
                        raise asyncio.CancelledError
