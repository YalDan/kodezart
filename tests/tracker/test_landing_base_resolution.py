"""The actual resolver consumes the observer's record through every tracker."""

import pytest

from kodezart.services.base_resolver import BaseResolver
from kodezart.types.domain.branch import WorkRef, WorkRefLanding, WorkRefRole
from tests.fakes import FakeGitService
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    ASSET_ISSUE,
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    fixture_server,
)

TRUNK = "scope-trunk"


@pytest.fixture
def blockers():
    return (CLAIMED_ISSUE, ASSET_ISSUE)


@pytest.fixture
def server(blockers):
    value = fixture_server()
    value.issues[APPROVED_ISSUE].relations = [("blockedBy", key) for key in blockers]
    return value


async def record(tracker, key, *, landing, sha="0000000"):
    await tracker.record_work_ref(
        ref=WorkRef(
            issue_id=key,
            role=WorkRefRole.DELIVERABLE,
            branch=f"work/{key}",
            pushed_head_sha=sha,
            landing=landing,
            recorded_at=FIXTURE_NOW,
        )
    )


async def resolve(tracker, git):
    return await BaseResolver(tracker=tracker, git=git, remote="upstream").resolve(
        issue_key=APPROVED_ISSUE,
        repo_path="/fixture/repo",
        integration_workspace="/fixture/integration",
        trunk=TRUNK,
        now=FIXTURE_NOW,
    )


@pytest.mark.parametrize("blockers", [(CLAIMED_ISSUE,), (CLAIMED_ISSUE, ASSET_ISSUE)])
@pytest.mark.parametrize("sha", [None, "0000000"])
async def test_recorded_landed_blockers_contribute_nothing_before_any_git_read(
    tracker, tracker_writes, blockers, sha
):
    for key in blockers:
        await record(tracker, key, landing=WorkRefLanding.LANDED, sha=sha)
    before = tracker_writes()
    git = FakeGitService(remote_branch_shas={f"work/{key}": None for key in blockers})
    result = await resolve(tracker, git)
    assert result.base_branch == TRUNK
    assert result.base_role is None
    assert result.inputs == ()
    assert git.calls == []
    assert tracker_writes() == before
