"""The reader that gathers a lane's entry facts, in the order it gathers them.

The decision itself is pure and is covered row by row in
``tests/domain/test_lane_entry.py``.  What can only be seen here is the order:
which refusals are made from the record alone, before a remote is asked
anything, and what the reader says out loud when the record and the remote
disagree about the head (KOD-684).
"""

import pytest
import structlog.testing

from kodezart.domain.errors import LaneEntryError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.lane_entry import ResumedLane
from kodezart.types.domain.run_state import LaneCommit, LaneRunState
from tests.chains.test_native_fire import native_operation
from tests.fakes import FakeGitService, FakeTrackerPort, make_tracker_issue

LANE = "KOD-684"
BASE = "trunk"
DELIVERABLE = "kodezart/KOD-684-0a1b2c3d"
LOOP = f"{DELIVERABLE}-ralph-11112222"
REMOTE = "fixture-remote"
RECORDED_HEAD = "a" * 40
REMOTE_HEAD = "c" * 40
DIGEST = "d" * 64
OPEN = ("KOD-684/check",)


def record(*, extra: tuple[BranchAssociation, ...] = ()) -> LaneRunState:
    """This lane's record, as the writer left it after a pushed commit."""
    return LaneRunState(
        lane_key=LANE,
        branch=LOOP,
        branch_url=f"https://forge.invalid/{LOOP}",
        head_sha=RECORDED_HEAD,
        pushed_head_sha=RECORDED_HEAD,
        commits_ahead=1,
        files_changed=1,
        commits=[LaneCommit(sha=RECORDED_HEAD, subject="feat: one", issue_id=LANE)],
        body_digest=DIGEST,
        associations=[
            BranchAssociation(
                branch=DELIVERABLE,
                role=BranchRole.DELIVERABLE,
                derived_from=BASE,
                run_id="first-job",
            ),
            BranchAssociation(
                branch=LOOP,
                role=BranchRole.LOOP,
                derived_from=DELIVERABLE,
                run_id="first-job",
            ),
            *extra,
        ],
    )


async def board(stored: LaneRunState) -> FakeTrackerPort:
    """The lane's issue carrying *stored* under the configured marker."""
    port = FakeTrackerPort(
        issues=[make_tracker_issue(LANE)],
        marker_prefixes=native_operation().marker_prefixes,
    )
    await port.post_comment(
        issue_key=LANE,
        body=render_lane_record(
            record=stored, marker_prefixes=native_operation().marker_prefixes
        ),
    )
    return port


def reader(port: FakeTrackerPort, git: FakeGitService) -> LaneEntryReader:
    return LaneEntryReader(
        records=LaneRecordReader(tracker=port, operation=native_operation()),
        git=git,
        remote=REMOTE,
    )


async def test_a_remote_head_past_the_record_resumes_there_and_says_so():
    """The remote is the truth about what the branch contains.

    The known cause is a commit pushed while its record write failed, so the
    lane resumes at the remote head and both shas are logged: a lane whose
    record is behind is a lane somebody has to be able to see.
    """
    port = await board(record())
    git = FakeGitService(remote_branch_shas={LOOP: REMOTE_HEAD})

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert entry == ResumedLane(
        deliverable_branch=DELIVERABLE,
        loop_branch=LOOP,
        head_sha=REMOTE_HEAD,
        body_digest=DIGEST,
    )
    differs = [entry for entry in logs if entry["event"] == "lane_record_head_differs"]
    assert len(differs) == 1
    assert differs[0]["lane"] == LANE
    assert differs[0]["branch"] == LOOP
    assert differs[0]["recorded_head"] == RECORDED_HEAD
    assert differs[0]["remote_head"] == REMOTE_HEAD


async def test_a_record_level_with_the_remote_says_nothing():
    """Not vacuous: the same reader logs nothing when the two agree."""
    port = await board(record())
    git = FakeGitService(remote_branch_shas={LOOP: RECORDED_HEAD})

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == RECORDED_HEAD
    assert [item for item in logs if item["event"] == "lane_record_head_differs"] == []


async def test_associations_that_settle_nothing_refuse_before_the_remote_read():
    """A fact of the record alone is asked before the git call it precedes.

    A record naming two deliverables for its branch cannot be entered however
    the remote answers, so the reader refuses without asking it: the git
    double records no call at all.
    """
    damaged = record(
        extra=(
            BranchAssociation(
                branch=LOOP,
                role=BranchRole.LOOP,
                derived_from="another-deliverable",
                run_id="second-job",
            ),
        )
    )
    port = await board(damaged)
    git = FakeGitService(remote_branch_shas={LOOP: REMOTE_HEAD})

    with pytest.raises(LaneEntryError, match="deliverable branches, not one"):
        await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert git.calls == []
