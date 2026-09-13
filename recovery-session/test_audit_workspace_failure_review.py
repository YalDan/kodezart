"""Independent programmer/cancellation identity through actual Git acquisition."""

import asyncio

import pytest

from tests.integration.test_audit_runtime_native import native_audit, repository, server
from tests.tracker.conftest import FIXTURE_NOW

__all__ = ["native_audit", "repository", "server"]


@pytest.mark.parametrize("kind", ["programmer", "cancel"])
async def test_actual_audit_worktree_acquisition_keeps_failure_identity(
    native_audit, monkeypatch, kind
):
    audit, executor, _backend, _tracker, _git, workspace, _repository = native_audit
    create = asyncio.create_subprocess_exec
    failure = (
        RuntimeError("independent subprocess implementation defect")
        if kind == "programmer"
        else asyncio.CancelledError("independent cancellation")
    )
    reached = False

    async def subprocess(*args, **kwargs):
        nonlocal reached
        if args[:3] == ("git", "worktree", "add"):
            reached = True
            raise failure
        return await create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", subprocess)
    with pytest.raises(type(failure)) as caught:
        await audit.run(FIXTURE_NOW)
    assert caught.value is failure
    assert reached
    assert not executor.calls
    assert not workspace._workspaces
