"""Actual claim cache and remote reads settle before exit or workspace release."""

from functools import partial

import pytest

from tests.git_read_cancellation import (
    assert_cache_read_settles,
    assert_git_read_settles_before_release,
)
from tests.tracker import test_audit_claim as fixtures

setup = fixtures.setup
server = fixtures.server


@pytest.mark.parametrize("existing", [False, True])
async def test_claim_cache_acquisition_settles_native_clone_or_fetch(
    setup, monkeypatch, tmp_path, existing
):
    build, runner, _, cache, workspace, _ = setup
    await assert_cache_read_settles(
        invoke=partial(build().verify, fixtures.REQUEST),
        cache=cache,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )
    assert not runner.calls and not workspace.calls


@pytest.mark.parametrize("read_number", [1, 2])
async def test_claim_remote_head_read_settles_before_return_or_release(
    setup, monkeypatch, tmp_path, read_number
):
    build, _, git, _, workspace, _ = setup
    await assert_git_read_settles_before_release(
        invoke=partial(build().verify, fixtures.REQUEST),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="remote_branch_sha",
        read_number=read_number,
        expect_release=read_number == 2,
    )
