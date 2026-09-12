"""Settle current workspace facts before cancellation releases their owner."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import GitService


async def read_workspace_head(*, git: GitService, workspace: str) -> tuple[str, bool]:
    """Return current commit and dirtiness before cancellation can release it."""

    async def observe() -> tuple[str, bool]:
        return await git.current_sha(workspace), await git.has_changes(workspace)

    observed = await settle(observe())
    return observed


async def read_remote_head(
    *, git: GitService, repository: str, remote: str, branch: str
) -> str | None:
    """Finish the native remote lookup before cancellation leaves its caller."""
    head = await settle(git.remote_branch_sha(repository, remote, branch))
    return head
