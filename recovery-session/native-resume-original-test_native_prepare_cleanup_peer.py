"""Actual pre-writer failures must settle their newly acquired worktree."""

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import FireSpecEntryError, GitOperationError
from kodezart.types.domain.native_execution import NewNativeExecution
from tests.chains.test_native_fire import tracker
from tests.chains.test_native_parent_resume import prepared_parent
from tests.services.test_native_amendments import Executor, repository

__all__ = ["repository"]


@pytest.mark.parametrize("cut", ["guard_begin", "workspace_capture"])
async def test_failed_preparation_releases_uncheckpointed_unwritten_workspace(
    repository, monkeypatch, cut
):
    port, saver = tracker(), InMemorySaver()
    executor = Executor(claim=False)
    fire, workspace, _, config = await prepared_parent(repository, executor, port, saver)
    observed_paths = set()
    actual_scan = port.scope_issues
    actual_identity = workspace._git.worktree_identity

    async def unavailable_begin(**kwargs):
        if workspace._workspaces:
            observed_paths.update(workspace._workspaces)
            raise ConnectionError("authority unavailable after acquisition")
        return await actual_scan(**kwargs)

    async def unavailable_capture(workspace_path, *, repository_path):
        identity = await actual_identity(workspace_path, repository_path=repository_path)
        assert identity.head_sha
        observed_paths.add(workspace_path)
        raise GitOperationError("identity readout unavailable before any writer")

    if cut == "guard_begin":
        monkeypatch.setattr(port, "scope_issues", unavailable_begin)
    else:
        monkeypatch.setattr(workspace._git, "worktree_identity", unavailable_capture)
    try:
        with pytest.raises((NativeWriteRefusalError, FireSpecEntryError, GitOperationError)):
            await fire.native_graph.ainvoke(None, config=config)
        assert observed_paths
        assert not executor.calls
        phases = [
            row.checkpoint["channel_values"]["execution"]
            for row in saver.list(None)
            if "execution" in row.checkpoint["channel_values"]
        ]
        assert phases and all(isinstance(phase, NewNativeExecution) for phase in phases)
        assert not port.comments
        assert not workspace._workspaces, "failed preparation retains uncheckpointed ownership"
        assert all(not Path(path).exists() for path in observed_paths)
    finally:
        monkeypatch.undo()
        for path in tuple(workspace._workspaces):
            await workspace.release(path)
