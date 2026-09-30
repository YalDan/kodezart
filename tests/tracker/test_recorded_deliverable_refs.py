"""The ref read of base resolution, served from a lane record (KOD-842).

Every case here is about one question — which ref delivers this issue — asked
of a record and answered without a work-ref comment anywhere on the board.
The record's own branch is the LOOP branch, so a reader that returned
``record.branch`` would be returning the wrong ref in every case; the
expected branch is written out here rather than derived from the record, so
the two statements are compared and not merely both derived from one.
"""

import pytest

from kodezart.domain.errors import LaneEntryError, LaneRecordReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_records import LaneRecordReader, RecordedDeliverableRefs
from kodezart.types.domain.branch import (
    BranchAssociation,
    BranchRole,
    WorkRefLanding,
    WorkRefRole,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneCommit, LaneRunState
from kodezart.types.domain.tracker import TrackerComment
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort

PREFIXES = {"run_state": "fixture-record"}
OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
)

#: The blocker whose branch a dependent lane would stand on.  Its lane key is
#: its issue key, which is what makes one address serve both reads.
BLOCKER = "KOD-842"
LOOP = "kodezart/KOD-842-0a1b2c3d-ralph-11112222"
DELIVERABLE = "kodezart/KOD-842-0a1b2c3d"
#: A second deliverable one record names beside the first.  Sorts BEFORE
#: ``DELIVERABLE``, so the refusal's tuple is asserted in one order and a
#: reader that answered in association order would be visible.
ANOTHER_DELIVERABLE = "another-deliverable"
BASE = "fixture-trunk"
HEAD = "a" * 40
#: Deliberately not the head: only what the remote holds can carry another
#: lane, so a reader answering with ``head_sha`` is visible rather than
#: plausible.
PUSHED = "b" * 40
WROTE_AT = FIXTURE_EPOCH


def record(
    *,
    lane: str = BLOCKER,
    deliverable: str | None = DELIVERABLE,
    pushed: str | None = PUSHED,
    extra: tuple[BranchAssociation, ...] = (),
) -> LaneRunState:
    """One lane's record, as the committing loop leaves it after a push.

    *deliverable* of ``None`` is a record whose loop association names no
    deliverable at all — the associations settle nothing, which is a fact of
    the record alone.  *extra* appends associations a later run left beside
    the first run's pair.
    """
    return LaneRunState(
        lane_key=lane,
        branch=LOOP,
        branch_url=f"https://forge.invalid/{LOOP}",
        head_sha=HEAD,
        pushed_head_sha=pushed,
        commits_ahead=1,
        files_changed=1,
        commits=[LaneCommit(sha=HEAD, subject="feat: one", issue_id=lane)],
        associations=[
            *(
                [
                    BranchAssociation(
                        branch=deliverable,
                        role=BranchRole.DELIVERABLE,
                        derived_from=BASE,
                        run_id="first-job",
                    )
                ]
                if deliverable is not None
                else []
            ),
            BranchAssociation(
                branch=LOOP,
                role=BranchRole.LOOP,
                derived_from=deliverable,
                run_id="first-job",
            ),
            *extra,
        ],
    )


def board(*, body: str | None = None, count: int = 1) -> FakeTrackerPort:
    """The blocker's issue, carrying *count* comments under the lane marker."""
    port = FakeTrackerPort(marker_prefixes=PREFIXES)
    content = (
        render_lane_record(record=record(), marker_prefixes=PREFIXES)
        if body is None
        else body
    )
    port.comments = [
        TrackerComment(
            comment_key=f"comment-{index}",
            issue_key=BLOCKER,
            author_key="fixture-writer",
            body=content,
            created_at=WROTE_AT,
        )
        for index in range(count)
    ]
    return port


def refs(port: FakeTrackerPort) -> RecordedDeliverableRefs:
    return RecordedDeliverableRefs(
        records=LaneRecordReader(tracker=port, operation=OPERATION)
    )


async def test_a_record_yields_its_deliverable_branch_by_role():
    """The ref names the deliverable branch, never the branch the record is on."""
    (ref,) = await refs(board()).work_refs(issue_key=BLOCKER)

    assert ref.branch == DELIVERABLE
    assert ref.branch != LOOP
    assert ref.issue_id == BLOCKER
    assert ref.role is WorkRefRole.DELIVERABLE
    assert ref.pushed_head_sha == PUSHED
    assert ref.recorded_at == WROTE_AT
    # Nothing observed this branch land, and nothing here invents that it
    # did: the resolver's unknown arm is the one this ref reaches.
    assert ref.landing is WorkRefLanding.UNKNOWN


async def test_no_record_yields_nothing():
    """A lane no comment addresses is a blocker with no ref, not a refusal."""
    assert (
        await refs(FakeTrackerPort(marker_prefixes=PREFIXES)).work_refs(
            issue_key=BLOCKER
        )
        == ()
    )


async def test_a_damaged_record_refuses_rather_than_reading_as_no_record():
    """Two comments under one marker settle nothing, and never settle "none"."""
    with pytest.raises(LaneRecordReadError):
        await refs(board(count=2)).work_refs(issue_key=BLOCKER)


async def test_a_record_whose_associations_settle_no_deliverable_refuses():
    """A loop branch naming no deliverable cannot say which ref delivers."""
    body = render_lane_record(record=record(deliverable=None), marker_prefixes=PREFIXES)
    with pytest.raises(LaneEntryError):
        await refs(board(body=body)).work_refs(issue_key=BLOCKER)


async def test_a_record_naming_two_deliverable_branches_refuses_naming_both():
    """Two deliverables settle nothing, and the refusal says which two.

    Read through the role, which is the only way base resolution asks: a
    reader that answered with whichever deliverable came first would resolve
    a base and look right, and one that refused without naming the two would
    leave a person with nothing to repair the record from.  The two LOOP
    associations are a real record's shape — each run records its own pair —
    so each deliverable carries the DELIVERABLE association naming its base.
    """
    body = render_lane_record(
        record=record(
            extra=(
                BranchAssociation(
                    branch=ANOTHER_DELIVERABLE,
                    role=BranchRole.DELIVERABLE,
                    derived_from=BASE,
                    run_id="second-job",
                ),
                BranchAssociation(
                    branch=LOOP,
                    role=BranchRole.LOOP,
                    derived_from=ANOTHER_DELIVERABLE,
                    run_id="second-job",
                ),
            )
        ),
        marker_prefixes=PREFIXES,
    )

    with pytest.raises(LaneEntryError) as caught:
        await refs(board(body=body)).work_refs(issue_key=BLOCKER)

    assert caught.value.branches == (ANOTHER_DELIVERABLE, DELIVERABLE)
    assert caught.value.issue_key == BLOCKER


async def test_an_unpushed_record_carries_no_sha_for_another_lane_to_stand_on():
    """``None`` stays ``None``: the resolver's own refusal reads it (KOD-842)."""
    body = render_lane_record(record=record(pushed=None), marker_prefixes=PREFIXES)
    (ref,) = await refs(board(body=body)).work_refs(issue_key=BLOCKER)

    assert ref.branch == DELIVERABLE
    assert ref.pushed_head_sha is None
