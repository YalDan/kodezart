"""A mandate session must observe the selected commit's original bytes."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.audit import AuditVerdict
from tests.audit_replacements import native_repository as native_repository
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.tracker.test_audit_mandate import REQUEST
from tests.tracker.test_audit_mandate import server as server
from tests.tracker.test_audit_mandate import setup as setup


@pytest.mark.parametrize("phase", ["clean", "before", "during"])
@pytest.mark.parametrize("namespace", ["default", "configured"])
async def test_actual_mandate_reuses_cache_without_accepting_replaced_bytes(
    setup, native_repository, monkeypatch, tracker_writes, phase, namespace
):
    native = native_repository
    if namespace == "configured":
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/audit-replacement/")
    build, runner, *_ = setup
    verifier = build()
    verifier._git = native.git
    verifier._workspace = native.workspace
    request = REQUEST.model_copy(
        update={
            "repo_url": native.source.as_uri(),
            "cache_key": "audit-cache",
            "claim": REQUEST.claim.model_copy(update={"head_sha": native.head}),
        }
    )
    acquired = []
    original = native.workspace.acquire

    async def acquire(**kwargs):
        path = await original(**kwargs)
        acquired.append(path)
        assert kwargs["ref"] == native.head and kwargs["create_branch"] is False
        assert await native.git.current_sha(path) == native.head
        assert not await native.git.has_changes(path)
        expected = "Substituted.\n" if phase == "before" else "Original.\n"
        assert (Path(path) / "evidence.txt").read_text() == expected
        return path

    async def during():
        await native.replace(runner.arguments["workspace_path"])

    monkeypatch.setattr(native.workspace, "acquire", acquire)
    if phase == "before":
        await native.replace()
    elif phase == "during":
        runner.during = during
    writes = tracker_writes()
    if phase == "clean":
        result = await verifier.complete(request)
        assert result.claim.head_sha == native.head
        assert result.mandate.verdict is AuditVerdict.HOLDS
        assert runner.arguments["session_id"] is None
    else:
        with pytest.raises(AuditClaimReadError, match="substitut"):
            await verifier.complete(request)
    assert bool(runner.calls) is (phase != "before")
    assert len(acquired) == 1 and not Path(acquired[0]).exists()
    assert not native.workspace._workspaces
    assert tracker_writes() == writes


@pytest.mark.parametrize("read_number", [1, 2])
async def test_unreadable_replacement_refs_cannot_return_mandate(
    setup, monkeypatch, tracker_writes, read_number
):
    build, runner, git, workspace = setup
    error = RuntimeError("replacement namespace could not be read")
    monkeypatch.setattr(
        git,
        "has_replace_refs",
        AsyncMock(side_effect=[False] * (read_number - 1) + [error]),
    )
    writes = tracker_writes()
    with pytest.raises(RuntimeError) as caught:
        await build().complete(REQUEST)
    assert caught.value is error
    assert bool(runner.calls) is (read_number == 2)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    assert tracker_writes() == writes


@pytest.mark.parametrize("read_number", [1, 2])
async def test_replacement_read_settles_before_mandate_workspace_release(
    setup, monkeypatch, tmp_path, read_number
):
    build, _, git, workspace = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().complete(REQUEST),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
    )
