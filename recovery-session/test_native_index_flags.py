"""Index flags that affect staging must remain part of saved native identity."""

import pytest

from kodezart.domain.errors import WorkspaceError
from tests.adapters.test_native_workspace_resume import acquired
from tests.services.test_native_amendments import git, repository

__all__ = ["repository"]

@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
async def test_native_resume_rejects_changed_index_flags(repository, flag):
    service, _cache, owner, workspace = await acquired(repository)
    snapshot = await owner.capture(workspace_path=workspace, holder="job")
    await git(workspace, "update-index", flag, "policy.py")
    try:
        with pytest.raises(WorkspaceError):
            await owner.resume(snapshot=snapshot, holder="job", repo_path=str(repository[0]), repo_url=None, cache_key=None)
    finally:
        await owner.release(workspace)
