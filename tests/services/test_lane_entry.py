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
from kodezart.domain.lane_entry import recorded_branches, recorded_commit
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
#: The best commit of a run that did not converge: a row after the head field.
BEST_COMMIT = "b" * 40
REMOTE_HEAD = "c" * 40
#: Where the base stands, and where a deliverable branch that has taken
#: nothing from its loop still stands with it.
BASE_TIP = "e" * 40
#: Where a deliverable branch that has taken work of its own stands instead.
MOVED_TIP = "f" * 40
DIGEST = "d" * 64


#: The three reads one record's re-entry makes, in the order it makes them:
#: one per branch its roles resolve, each answered by a sha of its own.
def reads(*branches: str) -> list[tuple[str, ...]]:
    return [("remote_branch_sha", "/clone", REMOTE, branch) for branch in branches]


OPEN = ("KOD-684/check",)


def record(
    *,
    rows: tuple[LaneCommit, ...] | None = None,
    extra: tuple[BranchAssociation, ...] = (),
) -> LaneRunState:
    """This lane's record, as the writer left it after a pushed commit.

    ``rows`` is the commit-act sequence; production writes it ending at the
    head field, and a case that gives its own is a lane whose record names a
    commit the head field does not.
    """
    return LaneRunState(
        lane_key=LANE,
        branch=LOOP,
        branch_url=f"https://forge.invalid/{LOOP}",
        head_sha=RECORDED_HEAD,
        pushed_head_sha=RECORDED_HEAD,
        commits_ahead=1,
        files_changed=1,
        commits=list(
            rows
            if rows is not None
            else (LaneCommit(sha=RECORDED_HEAD, subject="feat: one", issue_id=LANE),)
        ),
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
    git = FakeGitService(
        remote_branch_shas={LOOP: REMOTE_HEAD, DELIVERABLE: BASE_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert entry == ResumedLane(
        deliverable_branch=DELIVERABLE,
        loop_branch=LOOP,
        head_sha=REMOTE_HEAD,
        deliverable_head_sha=BASE_TIP,
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
    git = FakeGitService(
        remote_branch_shas={LOOP: RECORDED_HEAD, DELIVERABLE: BASE_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == RECORDED_HEAD
    assert [item for item in logs if item["event"] == "lane_record_head_differs"] == []
    # Level at both levels: the deliverable branch stands where its base does,
    # so that reading says nothing either.
    assert [
        item for item in logs if item["event"] == "lane_deliverable_head_differs"
    ] == []


async def test_a_non_convergent_lane_resolves_its_recorded_commit_by_sha():
    """The record is the only source of what this lane committed (KOD-705).

    The run did not converge: its best commit is the last row, which is
    neither the head field nor the loop tip the remote now holds. Re-entry
    resolves that commit through the loop level and reports it, while the
    deliverable branch still stands at its base tip — every fact by sha, none
    by branch name.
    """
    stored = record(
        rows=(
            LaneCommit(sha=RECORDED_HEAD, subject="feat: one", issue_id=LANE),
            LaneCommit(sha=BEST_COMMIT, subject="feat: two", issue_id=LANE),
        )
    )
    port = await board(stored)
    # The loop tip is a third sha, and the deliverable branch has taken
    # nothing yet: it is still where its base is.
    remote_shas: dict[str, str | None] = {
        LOOP: REMOTE_HEAD,
        DELIVERABLE: BASE_TIP,
        BASE: BASE_TIP,
    }
    git = FakeGitService(remote_branch_shas=remote_shas)

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    # One read per branch the record's roles resolve, in that order: the loop
    # level, the deliverable level, and the base the deliverable level is
    # compared against. Every conjunct of this case is therefore answered by a
    # sha somebody read, not by a fixture value nothing addresses.
    assert git.calls == reads(LOOP, DELIVERABLE, BASE)
    # The domain function is called once, for the expected value only: the
    # record it answers over is the one this test built, while the reader
    # answered over the one it parsed back out of the comment, so every
    # assertion below is on the reader's output and none restates the
    # function beside itself.
    expected = recorded_commit(
        record=stored, branches=recorded_branches(record=stored)
    ).sha
    differs = [item for item in logs if item["event"] == "lane_record_head_differs"]
    assert len(differs) == 1
    assert differs[0]["recorded_head"] == expected
    assert differs[0]["recorded_head"] == BEST_COMMIT
    assert differs[0]["remote_head"] == REMOTE_HEAD
    # And the commit the reader reports is neither the base tip the
    # deliverable branch sits on nor the loop tip the remote holds — by sha,
    # read off the reader's own output rather than off the fixture's dict.
    assert differs[0]["recorded_head"] not in (BASE_TIP, REMOTE_HEAD)
    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == REMOTE_HEAD
    assert entry.deliverable_branch != entry.loop_branch
    # The other half, "the deliverable branch is still at its base tip", read
    # off the entry by sha: the branch the DELIVERABLE role resolves stands at
    # the base tip, which is neither the loop tip nor the commit the record
    # names, and the reader said nothing about it having moved.
    assert entry.deliverable_head_sha == BASE_TIP
    assert entry.deliverable_head_sha not in (REMOTE_HEAD, BEST_COMMIT)
    assert [
        item for item in logs if item["event"] == "lane_deliverable_head_differs"
    ] == []


async def test_a_deliverable_branch_at_its_base_tip_is_reported_by_sha():
    """The deliverable level answers for itself, by sha (KOD-705).

    A lane whose deliverable branch has taken nothing from its loop stands
    where the base its record names stands. The reader asks that branch — the
    one the DELIVERABLE role resolves — and the entry carries the answer, so
    the fact is one somebody observed rather than one a fixture asserts about a
    branch nothing addressed.
    """
    port = await board(record())
    git = FakeGitService(
        remote_branch_shas={LOOP: RECORDED_HEAD, DELIVERABLE: BASE_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert git.calls == reads(LOOP, DELIVERABLE, BASE)
    assert isinstance(entry, ResumedLane)
    assert entry.deliverable_head_sha == BASE_TIP
    # And the level itself is silent: there is nothing to say about a branch
    # that stands where it was cut.
    assert [
        item for item in logs if item["event"] == "lane_deliverable_head_differs"
    ] == []


async def test_a_deliverable_branch_off_its_base_tip_is_reported_and_said_out_loud():
    """A deliverable branch carrying work of its own enters and is announced.

    Not a refusal: the branch holds something the base does not, which is this
    lane's next question rather than a reason to strand it. The moved sha is on
    the entry and both shas are on the line, so a lane whose delivery has moved
    on is a lane somebody can see.
    """
    port = await board(record())
    git = FakeGitService(
        remote_branch_shas={LOOP: RECORDED_HEAD, DELIVERABLE: MOVED_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert isinstance(entry, ResumedLane)
    assert entry.deliverable_head_sha == MOVED_TIP != BASE_TIP
    moved = [item for item in logs if item["event"] == "lane_deliverable_head_differs"]
    assert len(moved) == 1
    assert moved[0]["lane"] == LANE
    assert moved[0]["branch"] == DELIVERABLE
    assert moved[0]["base"] == BASE
    assert moved[0]["base_head"] == BASE_TIP
    assert moved[0]["deliverable_head"] == MOVED_TIP


@pytest.mark.parametrize(
    "settles_nothing,reason",
    [
        pytest.param(
            {
                "extra": (
                    BranchAssociation(
                        branch=LOOP,
                        role=BranchRole.LOOP,
                        derived_from="another-deliverable",
                        run_id="second-job",
                    ),
                )
            },
            "deliverable branches, not one",
            id="two-deliverables-for-one-branch",
        ),
        pytest.param({"rows": ()}, "names no commit act", id="no-commit-act"),
    ],
)
async def test_associations_that_settle_nothing_refuse_before_the_remote_read(
    settles_nothing, reason
):
    """A fact of the record alone is asked before the git call it precedes.

    A record naming two deliverables for its branch cannot be entered however
    the remote answers, and neither can one naming no commit act: both are
    settled from the record alone, so the reader refuses without asking the
    remote anything, and the git double records no call at all. Asserting the
    empty call list rather than the refusal alone is what pins the order.
    """
    damaged = record(**settles_nothing)
    port = await board(damaged)
    git = FakeGitService(remote_branch_shas={LOOP: REMOTE_HEAD})

    with pytest.raises(LaneEntryError, match=reason):
        await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert git.calls == []
