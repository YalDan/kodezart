"""The native feasibility cache is owned before any workspace is acquired."""

from functools import partial

import pytest

from tests.git_read_cancellation import assert_cache_read_settles
from tests.tracker import test_tracker_feasibility as fixtures

setup = fixtures.setup
server = fixtures.server


@pytest.mark.parametrize("existing", [False, True])
async def test_feasibility_cache_acquisition_settles_native_clone_or_fetch(
    setup, monkeypatch, tmp_path, existing
):
    build, runner, _, cache, workspace = setup
    await assert_cache_read_settles(
        invoke=partial(build().validate, fixtures.REQUEST),
        cache=cache,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )
    assert not runner.arguments and not workspace.calls
