"""Settle Git reads that still own a workspace when their caller cancels."""

import asyncio

from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import GitService


async def read_remote_head(
    *, git: GitService, repository: str, remote: str, branch: str
) -> str | None:
    """Finish the native remote lookup before cancellation leaves its caller."""
    head, cancelled = await finish_owned(
        asyncio.create_task(git.remote_branch_sha(repository, remote, branch))
    )
    if cancelled:
        raise asyncio.CancelledError
    return head


async def read_workspace_head(*, git: GitService, workspace: str) -> tuple[str, bool]:
    """Return current commit and dirtiness before cancellation can release it."""

    async def observe() -> tuple[str, bool]:
        return await git.current_sha(workspace), await git.has_changes(workspace)

    observed, cancelled = await finish_owned(asyncio.create_task(observe()))
    if cancelled:
        raise asyncio.CancelledError
    return observed
