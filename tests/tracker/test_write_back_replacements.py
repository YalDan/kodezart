"""Inline verification never certifies bytes substituted for its selected SHA."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import WriteBackReadError
from kodezart.types.domain.audit import AuditVerdict
from tests.audit_replacements import native_repository as native_repository
from tests.chains.test_write_back_verifier import REQUEST
from tests.chains.test_write_back_verifier import server as server
from tests.chains.test_write_back_verifier import setup as setup
from tests.git_read_cancellation import assert_git_read_settles_before_release


@pytest.mark.parametrize(
    "phase", ["clean", "before", "write", "during", "repair", "final_session"]
)
@pytest.mark.parametrize("namespace", ["default", "configured"])
async def test_actual_write_back_never_certifies_replacement_bytes(
    setup, native_repository, monkeypatch, tracker_writes, phase, namespace
):
    native = native_repository
    if namespace == "configured":
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/audit-replacement/")
    build, runner, _, _, actions, write, repair = setup
    verifier = build()
    verifier._git = native.git
    verifier._workspace = native.workspace
    request = REQUEST.model_copy(
        update={
            "repo_url": native.source.as_uri(),
            "cache_key": "audit-cache",
            "head_sha": native.head,
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
        if phase == "final_session" and len(runner.arguments) == 1:
            return
        # Replacing once keeps the old consumer running through a later round.
        runner.during = None
        await native.replace(acquired[0])

    async def write_then_replace():
        await write()
        await native.replace(acquired[0])

    async def repair_then_replace(artifact, judgment):
        await repair(artifact, judgment)
        await native.replace(acquired[0])

    monkeypatch.setattr(native.workspace, "acquire", acquire)
    if phase == "before":
        await native.replace()
    elif phase in {"during", "final_session"}:
        runner.during = during
    writes = tracker_writes()
    operation = verifier.verify(
        request,
        write=write_then_replace if phase == "write" else write,
        repair=repair_then_replace if phase == "repair" else repair,
    )
    if phase == "clean":
        result = await operation
        assert result.verdict is AuditVerdict.HOLDS
        assert result.verified_artifact.content == "tests/existing.py passes"
        assert all(item["session_id"] is None for item in runner.arguments)
    else:
        with pytest.raises(WriteBackReadError, match="substitut"):
            await operation
    assert (
        len(runner.arguments)
        == {
            "clean": 2,
            "before": 0,
            "write": 0,
            "during": 1,
            "repair": 1,
            "final_session": 2,
        }[phase]
    )
    if phase == "before":
        assert actions == [] and tracker_writes() == writes
    elif phase in {"write", "during"}:
        assert actions == ["write"]
    else:
        assert len(actions) == 2 and actions[1][0] == "repair"
    assert len(acquired) == 1 and not Path(acquired[0]).exists()
    assert not native.workspace._workspaces


@pytest.mark.parametrize("read_number", [1, 2, 3, 4, 5])
async def test_unreadable_replacement_refs_cannot_certify_an_artifact(
    setup, monkeypatch, read_number
):
    build, _, git, workspace, actions, write, repair = setup
    error = RuntimeError("replacement namespace could not be read")
    monkeypatch.setattr(
        git,
        "has_replace_refs",
        AsyncMock(side_effect=[False] * (read_number - 1) + [error]),
    )
    with pytest.raises(RuntimeError) as caught:
        await build().verify(REQUEST, write=write, repair=repair)
    assert caught.value is error
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    if read_number == 1:
        assert actions == []


@pytest.mark.parametrize("read_number", [1, 2, 3, 4, 5])
async def test_replacement_read_settles_before_inline_workspace_release(
    setup, monkeypatch, tmp_path, read_number
):
    build, _, git, workspace, _, write, repair = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().verify(REQUEST, write=write, repair=repair),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="has_replace_refs",
        read_number=read_number,
    )
