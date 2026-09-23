"""The reader that gathers a lane's entry facts, in the order it gathers them.

The decision itself is pure and is covered row by row in
``tests/domain/test_lane_entry.py``.  What can only be seen here is the order:
which refusals are made from the record alone, before a remote is asked
anything, and what the reader says out loud when the record and the remote
disagree about the head (KOD-684), and which head a resumed lane is handed:
always the one its record names, never a remote reading (KOD-705).
"""

import pytest
import structlog.testing

from kodezart.domain.errors import LaneEntryError
from kodezart.domain.lane_entry import recorded_commit
from kodezart.domain.lane_record import (
    LANDING_ROW_SUBJECT,
    next_lane_record,
    render_lane_record,
)
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.lane_entry import ResumedLane
from kodezart.types.domain.run_state import LaneBinding, LaneCommit, LaneRunState
from tests.chains.test_native_fire import native_operation
from tests.fakes import FakeGitService, FakeTrackerPort, make_tracker_issue

LANE = "KOD-684"
BASE = "trunk"
DELIVERABLE = "kodezart/KOD-684-0a1b2c3d"
LOOP = f"{DELIVERABLE}-ralph-11112222"
REMOTE = "fixture-remote"
RECORDED_HEAD = "a" * 40
#: A commit act recorded after the head field's own sha: the state only a
#: record nothing composed can be in, and the one the head-field case needs.
BEST_COMMIT = "b" * 40
REMOTE_HEAD = "c" * 40
#: Where the base stands, and where a deliverable branch that has taken
#: nothing from its loop still stands with it.
BASE_TIP = "e" * 40
#: Where a deliverable branch that has taken work of its own stands instead.
MOVED_TIP = "f" * 40
DIGEST = "d" * 64
#: The commit acts of a run that did not converge, in the order it made them:
#: the best of them by the trajectory's own rule is the second, and the third
#: is the tip the run slipped back to.
ACTS = ("1" * 40, "2" * 40, "3" * 40)
#: What the stall landing consolidated onto the deliverable branch, and where
#: that branch then stood: the best act, not the tip after it.
LANDED = ACTS[1]
PRE_LANDING_TIP = ACTS[2]


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

    ``rows`` is the commit-act sequence, and it is given only by the one case
    about a record whose last act the head field disagrees with. Every row the
    one constructor composes carries the head it was written at, so no
    reachable record holds that disagreement, and no reachable record can say
    which of the two the comparison is made against; ``composed`` below is what
    every other case is built by.
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


def composed(*acts: str, landed: str | None = None) -> LaneRunState:
    """This lane's record, composed act by act the only way production is.

    Every row goes through ``next_lane_record`` (KOD-685), so the value is one
    the writer could have left: the rows are the acts in the order they were
    made, the head field is the last of them, and the push is the last one the
    loop observed on its own branch. *landed* is the stall exit's recorded
    best act — the consolidated tip when the landing integrated, and otherwise
    the best commit itself — which the loop branch's own push is untouched by.
    """
    binding = LaneBinding(
        lane_key=LANE,
        loop_branch=LOOP,
        deliverable_branch=DELIVERABLE,
        base_ref=BASE,
        body_digest=DIGEST,
        repo_url=None,
        repo_path=None,
        run_id="first-job",
        visibility=RepoVisibility.PRIVATE,
    )
    state: LaneRunState | None = None
    for index, sha in enumerate(acts):
        state = next_lane_record(
            prior=state,
            lane=binding,
            branch_url=f"https://forge.invalid/{LOOP}",
            head_sha=sha,
            pushed_head_sha=sha,
            changeset=ChangesetDigest(
                file_paths=[f"lane-{step}.py" for step in range(index + 1)],
                commit_subjects=[f"feat: act {step + 1}" for step in range(index + 1)],
                commit_count=index + 1,
            ),
            subject=f"feat: act {index + 1}",
        )
    if landed is None:
        assert state is not None
        return state
    assert state is not None
    return next_lane_record(
        prior=state,
        lane=binding,
        branch_url=f"https://forge.invalid/{LOOP}",
        head_sha=landed,
        # The landing moved no branch of this lane's own, so the push stands
        # where the loop left it.
        pushed_head_sha=state.pushed_head_sha,
        changeset=ChangesetDigest(file_paths=[], commit_subjects=[], commit_count=0),
        subject=LANDING_ROW_SUBJECT,
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


async def test_a_loop_branch_past_the_record_is_not_resumed_from_and_is_said_out_loud():
    """The record is the truth about where the lane stands (KOD-705, KOD-96).

    A loop branch the remote holds past the record's head — the known cause is
    a commit pushed while its record write failed — is not resumed from: the
    entry carries the head the record names and no loop branch, so the fire
    cuts a fresh loop branch from that head and the unrecorded commit stays on
    the old branch, where it stands. Both shas are logged: a lane whose record
    is behind is a lane somebody has to be able to see.
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
        loop_branch=None,
        head_sha=RECORDED_HEAD,
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
    assert entry.loop_branch == LOOP
    assert [item for item in logs if item["event"] == "lane_record_head_differs"] == []
    # Level at both levels: the deliverable branch stands where its base does,
    # so that reading says nothing either.
    assert [
        item for item in logs if item["event"] == "lane_deliverable_head_differs"
    ] == []


async def test_a_remote_head_at_the_last_row_says_nothing_whatever_the_head_field():
    """What the remote head is compared WITH is the commit the rows name.

    The remote stands exactly at the last commit act, and the head field names
    an earlier one. There is nothing to report: the lane's recorded commit is
    the one the remote holds. A comparison made against the head field instead
    would announce a difference here — which is the whole point of the field
    not being the thing compared.
    """
    stored = record(
        rows=(
            LaneCommit(sha=RECORDED_HEAD, subject="feat: one", issue_id=LANE),
            LaneCommit(sha=BEST_COMMIT, subject="feat: two", issue_id=LANE),
        )
    )
    port = await board(stored)
    git = FakeGitService(
        remote_branch_shas={LOOP: BEST_COMMIT, DELIVERABLE: BASE_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert [item for item in logs if item["event"] == "lane_record_head_differs"] == []
    assert isinstance(entry, ResumedLane)
    assert entry.head_sha == BEST_COMMIT != stored.head_sha
    assert entry.loop_branch == LOOP


async def test_a_non_convergent_lane_resolves_its_recorded_commit_by_sha():
    """The landed case: re-entry resumes at the landed best, by sha (KOD-705).

    The run did not converge, and the stall landing consolidated the best of
    its acts onto the deliverable branch, so this case's premise is the
    deliverable branch standing at the landed sha. The record is composed act
    by act through the one constructor, so this is a record production writes
    — which is the whole reason it is the pin: a hand-built record whose rows
    the writer never composes would put the reader in a state no lane is ever
    in, and a reading that only held there would say nothing about any lane.

    Re-entry resolves the landing act through the loop level and resumes the
    lane there. The loop branch still stands at the tip the run slipped back
    to, which is not the answer: resuming there would be resuming from the
    work the landing was chosen over, so the entry carries no loop branch and
    the fire cuts a fresh one from the landed sha. Every fact by sha, none by
    branch name.
    """
    stored = composed(*ACTS, landed=LANDED)
    port = await board(stored)
    # Where the two levels stand after the landing: the loop branch is at the
    # tip the run slipped back to, and the deliverable branch is at the act the
    # landing put on it.
    remote_shas: dict[str, str | None] = {
        LOOP: PRE_LANDING_TIP,
        DELIVERABLE: LANDED,
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
    assert entry == ResumedLane(
        deliverable_branch=DELIVERABLE,
        loop_branch=None,
        head_sha=LANDED,
        deliverable_head_sha=LANDED,
        body_digest=DIGEST,
    )
    assert entry.head_sha != PRE_LANDING_TIP
    # The domain function is called once, for the expected value only: the
    # record it answers over is the one this test built, while the reader
    # answered over the one it parsed back out of the comment.
    assert entry.head_sha == recorded_commit(record=stored).sha
    # The landed act is a row of this record and not its first: the resolution
    # answers with the last act, and the act the run opened with is not it.
    assert [row.sha for row in stored.commits] == [*ACTS, LANDED]
    differs = [item for item in logs if item["event"] == "lane_record_head_differs"]
    assert len(differs) == 1
    assert (
        differs[0]["branch"],
        differs[0]["recorded_head"],
        differs[0]["remote_head"],
    ) == (LOOP, LANDED, PRE_LANDING_TIP)
    # The other level: the branch the DELIVERABLE role resolves now holds what
    # the landing put there, so it stands off the base it was cut from and the
    # reader says so.
    moved = [item for item in logs if item["event"] == "lane_deliverable_head_differs"]
    assert len(moved) == 1
    assert (moved[0]["base_head"], moved[0]["deliverable_head"]) == (BASE_TIP, LANDED)


async def test_a_stall_whose_deliverable_is_at_base_tip_resumes_at_its_best_iteration():
    """The stall case: the deliverable at its base tip is this case's premise.

    Observed by sha, at the branch the DELIVERABLE role resolves. The run did
    not converge and its landing put nothing on the deliverable branch, so the
    stall exit recorded the best commit itself as the lane's last act. That
    commit is not the loop tip the run slipped back to: re-entry resolves it
    through the loop level, finds the loop branch standing past it, and so
    carries no loop branch, and the fire cuts a fresh one from the best
    commit (KOD-705).
    """
    stored = composed(*ACTS, landed=ACTS[1])
    port = await board(stored)
    git = FakeGitService(
        remote_branch_shas={LOOP: ACTS[-1], DELIVERABLE: BASE_TIP, BASE: BASE_TIP}
    )

    with structlog.testing.capture_logs() as logs:
        entry = await reader(port, git).read(
            issue_key=LANE, open_criteria=OPEN, repo_path="/clone", resolved_base=BASE
        )

    assert git.calls == reads(LOOP, DELIVERABLE, BASE)
    assert entry == ResumedLane(
        deliverable_branch=DELIVERABLE,
        loop_branch=None,
        head_sha=ACTS[1],
        deliverable_head_sha=BASE_TIP,
        body_digest=DIGEST,
    )
    assert entry.head_sha != ACTS[-1]
    differs = [item for item in logs if item["event"] == "lane_record_head_differs"]
    assert len(differs) == 1
    assert (
        differs[0]["branch"],
        differs[0]["recorded_head"],
        differs[0]["remote_head"],
    ) == (LOOP, ACTS[1], ACTS[-1])
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
