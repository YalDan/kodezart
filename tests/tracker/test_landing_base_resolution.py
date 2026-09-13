"""The actual resolver consumes the observer's record through every tracker."""

import pytest

from kodezart.domain.errors import BaseResolutionError
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


@pytest.mark.parametrize("blockers", [(CLAIMED_ISSUE,)])
async def test_unknown_absent_raises_the_addressed_error_and_records_no_base(
    tracker, tracker_writes
):
    await record(tracker, CLAIMED_ISSUE, landing=WorkRefLanding.UNKNOWN)
    before = tracker_writes()
    git = FakeGitService(remote_branch_shas={f"work/{CLAIMED_ISSUE}": None})
    with pytest.raises(BaseResolutionError) as caught:
        await resolve(tracker, git)
    assert caught.value.issue_id == APPROVED_ISSUE
    assert caught.value.blocker_issue_ids == (CLAIMED_ISSUE,)
    assert caught.value.branches == (f"work/{CLAIMED_ISSUE}",)
    assert await tracker.read_base_spec(issue_key=APPROVED_ISSUE) is None
    assert tracker_writes() == before


@pytest.mark.parametrize(
    "retained", [WorkRefLanding.NOT_LANDED, WorkRefLanding.UNKNOWN]
)
@pytest.mark.parametrize("landed_key", [CLAIMED_ISSUE, ASSET_ISSUE])
async def test_mixed_inputs_select_only_the_unlanded_record(
    tracker, tracker_writes, blockers, retained, landed_key
):
    remaining = next(key for key in blockers if key != landed_key)
    await record(tracker, landed_key, landing=WorkRefLanding.LANDED)
    await record(tracker, remaining, landing=retained, sha="retained-head")
    git = FakeGitService(
        remote_branch_shas={
            f"work/{landed_key}": None,
            f"work/{remaining}": "retained-head",
        }
    )
    before = tracker_writes()
    result = await resolve(tracker, git)
    assert result.base_branch == f"work/{remaining}"
    assert result.base_role is WorkRefRole.DELIVERABLE
    assert [
        (item.blocker_issue_id, item.branch, item.sha) for item in result.inputs
    ] == [(remaining, f"work/{remaining}", "retained-head")]
    assert len(git.calls) == 1
    assert git.calls[0][0] == "remote_branch_sha"
    assert git.calls[0][-1] == f"work/{remaining}"
    assert tracker_writes() == before
