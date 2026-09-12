"""Settle current workspace facts before cancellation releases their owner."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import GitService


async def read_workspace_head(*, git: GitService, workspace: str) -> tuple[str, bool]:
    """Return current commit and dirtiness before cancellation can release it."""

    async def observe() -> tuple[str, bool]:
        return await git.current_sha(workspace), await git.has_changes(workspace)

    observed = await settle(observe())
    return observed
