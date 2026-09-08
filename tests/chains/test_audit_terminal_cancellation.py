"""Native terminal repository reads retain ownership through cancellation."""

from functools import partial

import pytest

from tests.chains import test_audit_pass as fixtures
from tests.fakes import FakeWorkspaceProvider
from tests.git_read_cancellation import (
    assert_cache_read_settles,
    assert_git_read_settles_before_release,
)

setup = fixtures.setup
server = fixtures.server
forge = fixtures.forge
tracker = fixtures.tracker
clock = fixtures.clock


@pytest.mark.parametrize("existing", [False, True])
async def test_terminal_cache_settles_native_clone_and_fetch(
    setup, monkeypatch, tmp_path, existing
):
    reader, *_ = setup
    await assert_cache_read_settles(
        invoke=partial(reader.observe, fixtures.REQUEST),
        cache=reader._cache,
        workspace=FakeWorkspaceProvider(),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )


@pytest.mark.parametrize("read_number", [1, 2, 3, 4])
async def test_terminal_branch_reads_settle_before_cancelled_return(
    setup, monkeypatch, tmp_path, read_number
):
    reader, git, *_ = setup
    await assert_git_read_settles_before_release(
        invoke=partial(reader.observe, fixtures.REQUEST),
        git=git,
        workspace=FakeWorkspaceProvider(),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase="remote_branch_sha",
        read_number=read_number,
        expect_release=False,
    )
