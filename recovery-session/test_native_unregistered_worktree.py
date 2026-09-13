"""A copied Git administrative pointer is not an acquired native worktree."""

import shutil
from pathlib import Path

import pytest

from kodezart.domain.errors import WorkspaceError
from tests.adapters.test_native_workspace_resume import acquired
from tests.services.test_native_amendments import repository

__all__ = ["repository"]


async def test_unregistered_copy_cannot_supply_native_worktree_identity(repository, tmp_path):
    service, _cache, owner, workspace = await acquired(repository)
    copied = tmp_path / "copied-native-worktree"
    shutil.copytree(workspace, copied, symlinks=True)
    assert (copied / ".git").read_bytes() == Path(workspace, ".git").read_bytes()
    try:
        with pytest.raises(WorkspaceError):
            await service.worktree_identity(str(copied), repository_path=str(repository[0]))
    finally:
        await owner.release(workspace)
