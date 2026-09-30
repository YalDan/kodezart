"""The scope heartbeat submits only what the board itself admits (KOD-1302).

The scan is a cheap question whose answer on one board is not stable, so it
only nominates. Approval and open work are read from the tracker in code;
these tests drive the heartbeat with a scan that lists a node and a board
that does or does not admit it, and read what reached the queue and the log.
"""

from collections.abc import Mapping, Sequence

import pytest
import structlog.testing

from kodezart.core.constants import DEFAULT_LANE
from kodezart.services.scope_heartbeat import ScopeHeartbeat
from kodezart.types.domain.agent import ScopeScanNode, ScopeScanOutput
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeJobQueue,
    FakeTrackerPort,
    make_tracker_issue,
)

REPO_URL = "https://example.invalid/example-org/example-repo"
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="scratch-project")
OTHER = ScopeRef(kind=ScopeKind.PROJECT, key="live-project")


def _board(
    *,
    members: Sequence[TrackerIssue],
    approved: bool = True,
    ref: ScopeRef = PROJECT,
    extra: dict[ScopeRef, Sequence[TrackerIssue]] | None = None,
) -> FakeTrackerPort:
    """A board holding *ref* with *members*, approved or not, plus *extra* scopes.

    Every extra scope is approved: they are the neighbours a rejected node
    must not starve.
    """
    scopes: dict[ScopeRef, tuple[Sequence[TrackerIssue], bool]] = {
        ref: (members, approved)
    }
    for other, rows in (extra or {}).items():
        scopes[other] = (rows, True)
    return FakeTrackerPort(
        issues=[issue for rows, _ in scopes.values() for issue in rows],
        scope_memberships={
            node: [issue.issue_key for issue in rows]
            for node, (rows, _) in scopes.items()
        },
        scope_containers=[
            ScopeContainer(
                ref=node,
                name=node.key,
                description="",
                url=f"https://tracker.invalid/project/{node.key}",
            )
            for node in scopes
        ],
        scope_label_members={
            node: frozenset({ScopeLabel.APPROVED})
            for node, (_, is_approved) in scopes.items()
            if is_approved
        },
    )


def _scan(*refs: ScopeRef) -> ScopeScanOutput:
    return ScopeScanOutput(
        scopes=[
            ScopeScanNode(
                kind=ref.kind,
                key=ref.key,
                repository=REPO_URL,
                why="the scan listed it",
            )
            for ref in refs
        ],
        reason="the scan listed what it listed",
    )


def _heartbeat(
    board: FakeTrackerPort, queue: FakeJobQueue, *answers: ScopeScanOutput
) -> ScopeHeartbeat:
    """A heartbeat whose scan answers *answers* in turn, one per tick."""
    pending = list(answers)

    async def ask() -> ScopeScanOutput | None:
        return pending.pop(0)

    return ScopeHeartbeat(
        ask=ask,
        tracker=board,
        registry=queue,
        queue=queue,
        trunks={REPO_URL: "main"},
    )


def _closed(key: str) -> TrackerIssue:
    return make_tracker_issue(
        key, state_name="Done", state_kind=WorkflowStateKind.COMPLETED
    )


def _rejections(logs: Sequence[Mapping[str, object]]) -> list[tuple[object, object]]:
    return [
        (entry["scope_key"], entry["reason"])
        for entry in logs
        if entry["event"] == "scope_heartbeat_scan_rejected"
    ]


async def test_an_approved_scope_with_open_work_is_submitted() -> None:
    queue = FakeJobQueue()
    board = _board(members=[_closed("S-1"), make_tracker_issue("S-2")])

    with structlog.testing.capture_logs() as logs:
        outcome = await _heartbeat(board, queue, _scan(PROJECT)).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    ((lane, request),) = queue.submissions
    assert lane == DEFAULT_LANE
    assert request.scope == PROJECT
    assert request.repo_url == REPO_URL
    assert _rejections(logs) == []
    assert [e["event"] for e in logs if e["event"].startswith("scope_heartbeat")] == [
        "scope_heartbeat_scanned",
        "scope_heartbeat_run_submitted",
    ]


async def test_an_approved_scope_whose_members_are_all_closed_is_rejected() -> None:
    """The observed case: every member done, the tracker record still open.

    Two ticks, the scan leaving the node out on the first and listing it on
    the second, as it did five minutes apart on 2026-09-29. The board did not
    change, and neither tick submits.
    """
    queue = FakeJobQueue()
    record = make_tracker_issue("S-3", issue_labels=frozenset({"tracker"}))
    canceled = make_tracker_issue(
        "S-4", state_name="Canceled", state_kind=WorkflowStateKind.CANCELED
    )
    board = _board(members=[_closed("S-1"), _closed("S-2"), record, canceled])
    heartbeat = _heartbeat(board, queue, _scan(), _scan(PROJECT))

    with structlog.testing.capture_logs() as logs:
        first = await heartbeat.run(FIXTURE_EPOCH)
        second = await heartbeat.run(FIXTURE_EPOCH)

    assert (first, second) == (PassRun.SKIPPED, PassRun.SKIPPED)
    assert queue.submissions == []
    (event,) = [e for e in logs if e["event"] == "scope_heartbeat_scan_rejected"]
    assert event == {
        "event": "scope_heartbeat_scan_rejected",
        "log_level": "info",
        "scope_kind": PROJECT.kind.value,
        "scope_key": PROJECT.key,
        "repo_url": REPO_URL,
        "reason": "no_open_member",
    }


async def test_an_approved_scope_with_no_member_at_all_is_rejected() -> None:
    queue = FakeJobQueue()

    with structlog.testing.capture_logs() as logs:
        outcome = await _heartbeat(_board(members=[]), queue, _scan(PROJECT)).run(
            FIXTURE_EPOCH
        )

    assert outcome is PassRun.SKIPPED
    assert queue.submissions == []
    assert _rejections(logs) == [(PROJECT.key, "no_open_member")]


async def test_a_scope_the_board_does_not_approve_is_rejected() -> None:
    """The scan's word is not approval: the label on the board is."""
    queue = FakeJobQueue()
    board = _board(members=[make_tracker_issue("S-1")], approved=False)

    with structlog.testing.capture_logs() as logs:
        outcome = await _heartbeat(board, queue, _scan(PROJECT)).run(FIXTURE_EPOCH)

    assert outcome is PassRun.SKIPPED
    assert queue.submissions == []
    assert _rejections(logs) == [(PROJECT.key, "not_approved")]


async def test_a_rejected_node_does_not_stop_the_next_one() -> None:
    queue = FakeJobQueue()
    board = _board(
        members=[_closed("S-1")],
        extra={OTHER: [make_tracker_issue("L-1")]},
    )

    with structlog.testing.capture_logs() as logs:
        outcome = await _heartbeat(board, queue, _scan(PROJECT, OTHER)).run(
            FIXTURE_EPOCH
        )

    assert outcome is PassRun.RAN
    assert [request.scope for _, request in queue.submissions] == [OTHER]
    assert _rejections(logs) == [(PROJECT.key, "no_open_member")]


@pytest.mark.parametrize(
    "kind",
    [
        WorkflowStateKind.TRIAGE,
        WorkflowStateKind.BACKLOG,
        WorkflowStateKind.UNSTARTED,
        WorkflowStateKind.STARTED,
    ],
)
async def test_a_member_in_any_state_that_still_owes_work_is_open(
    kind: WorkflowStateKind,
) -> None:
    """Open is what the one state rule says owes work, not Todo alone (KOD-443)."""
    queue = FakeJobQueue()
    member = make_tracker_issue("S-2", state_name=kind.value, state_kind=kind)
    board = _board(members=[_closed("S-1"), member])

    outcome = await _heartbeat(board, queue, _scan(PROJECT)).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    assert [request.scope for _, request in queue.submissions] == [PROJECT]


async def test_approval_granted_above_the_scope_admits_it() -> None:
    """Approval cascades down from a container, as a run's entry reads it."""
    queue = FakeJobQueue()
    initiative = ScopeRef(kind=ScopeKind.INITIATIVE, key="approved-initiative")
    member = make_tracker_issue("S-1")
    board = FakeTrackerPort(
        issues=[member],
        scope_memberships={PROJECT: [member.issue_key], initiative: []},
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name=PROJECT.key,
                description="",
                url=f"https://tracker.invalid/project/{PROJECT.key}",
                parent=initiative,
            ),
            ScopeContainer(
                ref=initiative,
                name=initiative.key,
                description="",
                url=f"https://tracker.invalid/initiative/{initiative.key}",
            ),
        ],
        scope_label_members={initiative: frozenset({ScopeLabel.APPROVED})},
    )

    with structlog.testing.capture_logs() as logs:
        outcome = await _heartbeat(board, queue, _scan(PROJECT)).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    assert [request.scope for _, request in queue.submissions] == [PROJECT]
    assert _rejections(logs) == []


async def test_each_tick_reads_the_board_afresh() -> None:
    """A rejection is not remembered: open work added later is submitted."""
    queue = FakeJobQueue()
    board = _board(members=[_closed("S-1")])
    heartbeat = _heartbeat(board, queue, _scan(PROJECT), _scan(PROJECT))

    first = await heartbeat.run(FIXTURE_EPOCH)
    reopened = make_tracker_issue("S-2")
    board.issues[reopened.issue_key] = reopened
    board.scope_memberships[PROJECT] = (*board.scope_memberships[PROJECT], "S-2")
    second = await heartbeat.run(FIXTURE_EPOCH)

    assert (first, second) == (PassRun.SKIPPED, PassRun.RAN)
    assert [request.scope for _, request in queue.submissions] == [PROJECT]
