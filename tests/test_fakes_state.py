"""The handover checks the write-nothing passes are held to, checked themselves.

``tests.fakes.handed_over`` and ``tests.fakes.nothing_written`` are what every
"this pass wrote nothing to the board" assertion in the suite comes down to:
the first renders the double's whole surface when the board is handed over and
compares it when the answer is asked for; the second compares the same
rendering projected onto the journals a write can land in, which is the claim
a consumer that legitimately reads the board can still make. An answer of
``True`` for a board something DID write to would make all of those assertions
agree with the write they exist to catch, and none of them would report it —
the failure is silent by construction, because a check that always answers
"untouched" looks exactly like a consumer that touched nothing.

So the answer is exercised here directly: one write per journal the check
declares it reaches, each through the port's own method, each asked to come
back ``False``. The journal is compared before and after as well, so a case
whose write landed somewhere else is not mistaken for a case that covered
its journal.
"""

from collections.abc import Awaitable, Callable, Mapping

import pytest

from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    MappingKind,
    MappingRef,
    WorkflowStateKind,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    TRACKER_WRITE_JOURNALS,
    FakeTrackerPort,
    handed_over,
    make_tracker_issue,
    nothing_written,
    tracker_state,
)

ISSUE = "KOD-1"
#: A second issue, in a state of its own, so the state a put-back names is
#: one the board already defines: a put-back to the issue's own state is
#: the double's no-op and would fill no journal.
OTHER = "KOD-2"
STARTED_STATE = "In Progress"
HOLDER = "fixture-holder"
LEASE_SECONDS = 60.0
CONTAINER = "fixture-container"


def board() -> FakeTrackerPort:
    """A board handed over with two issues on it and nothing else seeded.

    Neither issue carries a queue state or a classification, so the writes
    below are real writes rather than the double's no-ops, and no document
    is held, so the document ensure creates rather than adopting.
    """
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(ISSUE, queue_states=()),
            make_tracker_issue(
                OTHER,
                state_name=STARTED_STATE,
                state_kind=WorkflowStateKind.STARTED,
                queue_states=(),
            ),
        ],
    )


async def write_issue(port: FakeTrackerPort) -> None:
    current = await port.read_issue(issue_key=ISSUE)
    await port.edit_description(
        target=ISSUE, expected=current.body, replacement="a rewritten body"
    )


async def write_classification(port: FakeTrackerPort) -> None:
    await port.set_issue_classification(issue_key=ISSUE, classification="criterion")


async def write_comment(port: FakeTrackerPort) -> None:
    await port.post_comment(issue_key=ISSUE, body="a comment nobody asked for")


async def write_workflow_state(port: FakeTrackerPort) -> None:
    await port.set_workflow_state(issue_key=ISSUE, stage=LifecycleStage.IN_PROGRESS)


async def write_claim(port: FakeTrackerPort) -> None:
    await port.claim_issue(issue_key=ISSUE, holder=HOLDER, lease_seconds=LEASE_SECONDS)


async def write_renewal(port: FakeTrackerPort) -> None:
    # Journalled whether or not it is granted: an attempt is the write.
    await port.renew_claim(issue_key=ISSUE, holder=HOLDER, lease_seconds=LEASE_SECONDS)


def leased_surface() -> WritableSurface:
    """The one surface the lease rows below take and give back."""
    return WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE),
    )


async def write_surface_lease(port: FakeTrackerPort) -> None:
    await port.acquire_surfaces(
        surfaces=frozenset({leased_surface()}),
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
    )


async def write_claim_release(port: FakeTrackerPort) -> None:
    # Journalled whether or not anything was held: an attempt is the write.
    # This board holds no claim, so the release moves nothing else at all —
    # which is what makes the case about this journal and no other.
    await port.release_claim(issue_key=ISSUE, holder=HOLDER)


async def write_lease_release(port: FakeTrackerPort) -> None:
    # The same for a surface set: nothing is leased here, so giving one back
    # leaves ``leases`` where it was and the attempt is the only trace.
    await port.release_surfaces(surfaces=frozenset({leased_surface()}), holder=HOLDER)


async def write_issue_creation(port: FakeTrackerPort) -> None:
    # The port creates an issue only by minting one; a criterion under its
    # parent's child-set grant is the mint this board can take.
    await port.acquire_surfaces(
        surfaces=frozenset(
            {
                WritableSurface(
                    kind=SurfaceKind.CRITERION_CHILD_SET,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE),
                )
            }
        ),
        holder=HOLDER,
        lease_seconds=LEASE_SECONDS,
    )
    await port.create_criterion_if_absent(
        parent_key=ISSUE,
        title="a new criterion",
        check="a new condition holds",
        do="make it hold",
        holder=HOLDER,
    )


async def write_base_spec(port: FakeTrackerPort) -> None:
    await port.record_base_spec(issue_key=ISSUE, spec=trunk_base("fixture-trunk"))


async def write_queue_state(port: FakeTrackerPort) -> None:
    await port.set_queue_state(issue_key=ISSUE, state=QueueState.APPROVED)


async def write_restored_state(port: FakeTrackerPort) -> None:
    await port.restore_workflow_state(issue_key=ISSUE, state_name=STARTED_STATE)


async def write_document(port: FakeTrackerPort) -> None:
    # One ensure fills both halves of a document: its title and its body.
    await port.ensure_mappings(
        refs=[
            MappingRef(kind=MappingKind.DOCUMENT, name="a record", scope=CONTAINER),
        ],
    )


async def write_self_write(port: FakeTrackerPort) -> None:
    port.self_writes.record(issue_key=ISSUE, updated_at=FIXTURE_EPOCH)


#: One write per journal the check declares it reaches, through the method
#: that fills it.  Keyed by journal so a journal the double grows later has
#: to arrive with the write that fills it, rather than being covered by a
#: table that no longer mentions it.
WRITES: Mapping[str, Callable[[FakeTrackerPort], Awaitable[None]]] = {
    "issue_writes": write_issue,
    "classification_writes": write_classification,
    "comment_writes": write_comment,
    "workflow_writes": write_workflow_state,
    "claim_writes": write_claim,
    "lease_writes": write_surface_lease,
    "renewals": write_renewal,
    "issue_creations": write_issue_creation,
    "recorded_base_specs": write_base_spec,
    "queue_writes": write_queue_state,
    "restored_states": write_restored_state,
    "_documents": write_document,
    "document_titles": write_document,
    "self_writes": write_self_write,
    "claim_releases": write_claim_release,
    "lease_releases": write_lease_release,
}


def test_every_journal_the_check_reaches_is_written_to_here() -> None:
    """The table and the check's own list of journals are the same set."""
    assert set(WRITES) == set(TRACKER_WRITE_JOURNALS)


@pytest.mark.parametrize("journal", sorted(TRACKER_WRITE_JOURNALS))
async def test_a_write_on_any_journal_answers_that_the_board_was_touched(
    journal: str,
) -> None:
    """One write, anywhere the double records one, and the answer is False."""
    port = board()
    untouched = handed_over(port)
    unwritten = nothing_written(port)
    before = tracker_state(port)[journal]

    await WRITES[journal](port)

    # The write landed in the journal this case is about, so a False answer
    # is this journal's coverage rather than some other journal's.
    assert tracker_state(port)[journal] != before
    assert untouched() is False
    # The projection reaches this journal too: a narrowed one would agree.
    assert unwritten() is False


async def test_a_board_nothing_wrote_to_answers_that_it_is_untouched() -> None:
    """The other half: the checks are observations, not standing refusals.

    Answering False for an untouched board would fail every write-nothing
    case in the suite instead of the writes they are about.
    """
    port = board()

    untouched = handed_over(port)

    assert untouched() is True
    assert nothing_written(port)() is True


async def test_a_read_moves_the_whole_surface_but_not_the_write_set() -> None:
    """Where the two answerers part: a read is a touch, and it is not a write.

    A consumer that re-reads the board before every barrier cannot claim the
    whole surface stood still, and still owes the narrower claim.
    """
    port = board()
    untouched = handed_over(port)
    unwritten = nothing_written(port)

    await port.read_issue(issue_key=ISSUE)

    assert untouched() is False
    assert unwritten() is True
