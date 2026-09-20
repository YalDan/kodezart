"""One board of lanes, their criterion families and the records their loops left.

Shared by the observer's own tests and the scheduled tick's, so both describe
one board rather than two that happen to agree.
"""

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, render_lane_record
from kodezart.domain.run_alarm_record import MARKER_PURPOSE, run_alarm_marker
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.tally_supervisor import SIGNAL, TallySupervisor
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.run_alarm import LaneSubject
from kodezart.types.domain.run_state import LaneCommit, LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, make_tracker_issue

SCOPE = "scoped-project"
HEAD = "a" * 40
HOLDER = "kodezart/supervisor"
BOUND = 1
LEASE_SECONDS = 60.0
PREFIXES = {
    "run_state": "fixture-record",
    "run_event": "fixture-runevent",
    "run_alarm": "fixture-runalarm",
}
#: The criteria-stage label the board's lanes carry, as the walker's own
#: fixtures spell it, so a scoped read of this board selects them.
STAGED = "criteria-staged"

#: Every board built through this module, with the lanes it was built for, so a
#: surface assertion can be applied to every fixture rather than to the ones
#: that remembered to ask for it.
BOARDS: list[tuple[FakeTrackerPort, tuple[str, ...]]] = []


def checks(lane):
    """The two criterion sub-issue keys every lane of this board owes."""
    return (f"{lane}/check", f"{lane}/second")


def subject(lane):
    return LaneSubject(scope_key=SCOPE, lane_key=lane)


def criterion(key, *, lane, closed=False):
    return make_tracker_issue(
        key,
        parent_key=lane,
        issue_labels=frozenset({"criterion"}),
        state_name="Done" if closed else "Todo",
        state_kind=WorkflowStateKind.COMPLETED
        if closed
        else WorkflowStateKind.UNSTARTED,
    )


def subtree(lane, *, closed=()):
    """The lane's whole criterion roster, with *closed* marked finished."""
    return tuple(
        criterion(key, lane=lane, closed=key in closed) for key in checks(lane)
    )


def still_open(lane, *, closed=()):
    return tuple(
        row for row in subtree(lane, closed=closed) if row.issue_key not in closed
    )


def lane_state(lane, *, commits):
    """The record the committing loop leaves on *lane*'s own issue."""
    loop = f"kodezart/{lane}-loop"
    deliverable = f"kodezart/{lane}"
    return LaneRunState(
        lane_key=lane,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=HEAD,
        pushed_head_sha=HEAD,
        commits_ahead=len(commits),
        files_changed=len(commits),
        commits=[
            LaneCommit(sha=sha, subject="feat: one", issue_id=lane) for sha in commits
        ],
        body_digest=None,
        associations=[
            BranchAssociation(
                branch=deliverable,
                role=BranchRole.DELIVERABLE,
                derived_from="trunk",
                run_id="first-job",
            ),
            BranchAssociation(
                branch=loop,
                role=BranchRole.LOOP,
                derived_from=deliverable,
                run_id="first-job",
            ),
        ],
    )


async def board(
    *, lanes, commits=("sha-one", "sha-two"), prefixes=PREFIXES, scope=None
):
    """Each lane's issue, its criterion family, and the record its loop left.

    With *scope* the same board is also addressable as that scope: the lanes
    are its members and each is approved, so the walker's own ready read
    answers for it and a composed tick can read this board rather than a
    second one written to agree with it.
    """
    port = FakeTrackerPort(
        issues=[
            row
            for lane in lanes
            for row in (
                make_tracker_issue(
                    lane,
                    issue_labels=frozenset() if scope is None else frozenset({STAGED}),
                ),
                *subtree(lane),
            )
        ],
        marker_prefixes=prefixes,
        scope_memberships=None if scope is None else {scope: tuple(lanes)},
        criteria_stage_label_key=None if scope is None else STAGED,
        scope_label_members=None
        if scope is None
        else {
            ScopeRef(kind=ScopeKind.ISSUE, key=lane): frozenset({ScopeLabel.APPROVED})
            for lane in lanes
        },
    )
    for lane in lanes:
        await port.post_comment(
            issue_key=lane,
            body=render_lane_record(
                record=lane_state(lane, commits=commits), marker_prefixes=PREFIXES
            ),
        )
    port.comment_writes.clear()
    BOARDS.append((port, tuple(lanes)))
    return port


def rewrite_record(port, lane, *, commits):
    """The record is one surface rewritten in place, as its own writer leaves it."""
    body = render_lane_record(
        record=lane_state(lane, commits=commits), marker_prefixes=PREFIXES
    )
    marker = compose_comment_marker(
        prefixes=PREFIXES, purpose=RUN_STATE_PURPOSE, lane=lane
    )
    index = next(
        position
        for position, row in enumerate(port.comments)
        if row.issue_key == lane and row.body.startswith(marker)
    )
    port.comments[index] = port.comments[index].model_copy(update={"body": body})


def operation(prefixes=PREFIXES):
    return OperationConfig(
        operation_name="fixture", workspace="fixture", marker_prefixes=prefixes
    )


def supervisor(port, *, bound=BOUND, holder=HOLDER):
    return TallySupervisor(
        tracker=port,
        records=LaneRecordReader(tracker=port, operation=operation()),
        marker_prefixes=port.marker_prefixes,
        max_commits_without_closure=bound,
        holder=holder,
        lease_seconds=LEASE_SECONDS,
    )


def alarm_marker(port, lane):
    return run_alarm_marker(
        subject=subject(lane), signal=SIGNAL, marker_prefixes=port.marker_prefixes
    )


def records_on(port, lane):
    return [
        row
        for row in port.comments
        if row.issue_key == lane and row.body.startswith(alarm_marker(port, lane))
    ]


async def events_on(port, lane):
    return list(await port.lane_run_events(issue_key=lane, lane_key=lane))


def snapshot(port):
    """What the board holds and what was written to it, as one comparable value."""
    return (list(port.comments), list(port.comment_writes), list(port.lease_writes))


def assert_every_write_is_inside_the_declared_set():
    """Every board built this test: nothing written outside the lane's own set.

    The set is derived from the purposes the board actually configures, so a
    board declaring no alarm prefix is checked against what it does declare
    rather than skipped.
    """
    for port, lanes in BOARDS:
        declared = [
            compose_comment_marker(
                prefixes=port.marker_prefixes, purpose=purpose, lane=lane
            )
            for lane in lanes
            for purpose in (RUN_EVENT_PURPOSE, RUN_STATE_PURPOSE)
        ]
        if MARKER_PURPOSE in port.marker_prefixes:
            declared.extend(alarm_marker(port, lane) for lane in lanes)
        assert all(row.body.startswith(tuple(declared)) for row in port.comments), [
            row.body.split("\n", 1)[0] for row in port.comments
        ]
        assert port.issue_writes == []
        assert port.workflow_writes == []
        assert port.restored_states == []
        assert port.queue_writes == []
        assert port.classification_writes == []
        assert port.claim_writes == []
        assert [lease for lease in port.leases.values() if lease.holder == HOLDER] == []
