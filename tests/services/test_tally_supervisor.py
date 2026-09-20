"""One record and transition-only events, over the in-process tracker double."""

import pytest

from kodezart.domain.lane_record import render_lane_record
from kodezart.domain.run_alarm_record import run_alarm_marker
from kodezart.domain.tally_record import is_raised
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.tally_supervisor import SIGNAL, TallySupervisor
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.run_alarm import LaneSubject
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneCommit, LaneRunState
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, make_tracker_issue

SCOPE = "scoped-project"
LANE = "LANE-1"
FIRST = f"{LANE}/check"
SECOND = f"{LANE}/second"
HEAD = "a" * 40
HOLDER = "kodezart/supervisor"
BOUND = 1
LEASE_SECONDS = 60.0
PREFIXES = {
    "run_state": "fixture-record",
    "run_event": "fixture-runevent",
    "run_alarm": "fixture-runalarm",
}
SUBJECT = LaneSubject(scope_key=SCOPE, lane_key=LANE)


def criterion(key, *, closed=False):
    return make_tracker_issue(
        key,
        parent_key=LANE,
        issue_labels=frozenset({"criterion"}),
        state_name="Done" if closed else "Todo",
        state_kind=WorkflowStateKind.COMPLETED
        if closed
        else WorkflowStateKind.UNSTARTED,
    )


def subtree(*, closed=()):
    """The lane's whole criterion roster, with *closed* marked finished."""
    return tuple(criterion(key, closed=key in closed) for key in (FIRST, SECOND))


def still_open(*, closed=()):
    return tuple(row for row in subtree(closed=closed) if row.issue_key not in closed)


def lane_state(*, commits):
    loop = f"kodezart/{LANE}-loop"
    deliverable = f"kodezart/{LANE}"
    return LaneRunState(
        lane_key=LANE,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=HEAD,
        pushed_head_sha=HEAD,
        commits_ahead=len(commits),
        files_changed=len(commits),
        commits=[
            LaneCommit(sha=sha, subject="feat: one", issue_id=LANE) for sha in commits
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


async def board(*, commits=("sha-one", "sha-two"), prefixes=PREFIXES):
    """A lane issue, its criterion family, and the record its loop left."""
    port = FakeTrackerPort(
        issues=[make_tracker_issue(LANE), *subtree()],
        marker_prefixes=prefixes,
    )
    await port.post_comment(
        issue_key=LANE,
        body=render_lane_record(
            record=lane_state(commits=commits), marker_prefixes=PREFIXES
        ),
    )
    port.comment_writes.clear()
    return port


def supervisor(port, *, bound=BOUND, holder=HOLDER):
    operation = OperationConfig(
        operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
    )
    return TallySupervisor(
        tracker=port,
        records=LaneRecordReader(tracker=port, operation=operation),
        marker_prefixes=port.marker_prefixes,
        max_commits_without_closure=bound,
        holder=holder,
        lease_seconds=LEASE_SECONDS,
    )


async def observe(tally, *, closed=()):
    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=subtree(closed=closed),
        gap=still_open(closed=closed),
        criteria=subtree(closed=closed),
    )


def rewrite_record(port, *, commits):
    """The lane's record is one surface rewritten in place, as its writer leaves it."""
    body = render_lane_record(
        record=lane_state(commits=commits), marker_prefixes=PREFIXES
    )
    index = next(
        position
        for position, row in enumerate(port.comments)
        if row.body.startswith(f"[{PREFIXES['run_state']}:")
    )
    port.comments[index] = port.comments[index].model_copy(update={"body": body})


def alarm_marker(port):
    return run_alarm_marker(
        subject=SUBJECT, signal=SIGNAL, marker_prefixes=port.marker_prefixes
    )


def records_on(port):
    return [row for row in port.comments if row.body.startswith(alarm_marker(port))]


async def events_on(port):
    return list(await port.lane_run_events(issue_key=LANE, lane_key=LANE))


def snapshot(port):
    return (
        list(port.comments),
        list(port.comment_writes),
        list(port.lease_writes),
    )


async def test_a_stall_across_many_ticks_leaves_one_record_and_one_raised_event():
    port = await board()
    tally = supervisor(port)

    await observe(tally)
    after_first = snapshot(port)
    for _ in range(4):
        await observe(tally)
        assert snapshot(port) == after_first

    assert len(records_on(port)) == 1
    assert [event.kind for event in await events_on(port)] == [
        RunEventKind.RUN_ALARM_RAISED
    ]
    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert is_raised(stored)
    assert stored.raised_by == HOLDER
    assert stored.raised_at_sha == HEAD


async def test_the_marker_prefix_comes_from_operation_configuration():
    other = {**PREFIXES, "run_alarm": "another-alarm"}
    port = await board(prefixes=other)

    await observe(supervisor(port))

    assert len(records_on(port)) == 1
    assert records_on(port)[0].body.startswith("[another-alarm:")


async def test_an_absent_alarm_prefix_refuses_before_any_read(monkeypatch):
    port = await board(prefixes={k: v for k, v in PREFIXES.items() if k != "run_alarm"})
    reads: list[str] = []
    listing = port.list_comments

    async def counted(*, issue_key: str):
        reads.append(issue_key)
        return await listing(issue_key=issue_key)

    monkeypatch.setattr(port, "list_comments", counted)
    before = snapshot(port)

    with pytest.raises(OperationMemberAbsentError):
        await observe(supervisor(port))

    assert reads == []
    assert snapshot(port) == before


async def test_clearing_edits_the_record_and_posts_one_cleared_event():
    port = await board()
    tally = supervisor(port)
    await observe(tally)
    raised = records_on(port)[0]

    await observe(tally, closed=(SECOND,))

    assert [row.comment_key for row in records_on(port)] == [raised.comment_key]
    assert [event.kind for event in await events_on(port)] == [
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
    ]
    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert not is_raised(stored)
    assert stored.readings[2].value.value == (SECOND,)

    after = snapshot(port)
    await observe(tally, closed=(SECOND,))
    assert snapshot(port) == after


async def test_a_second_stall_after_a_clear_is_measured_from_the_clear():
    port = await board()
    tally = supervisor(port)
    await observe(tally)
    await observe(tally, closed=(SECOND,))

    rewrite_record(port, commits=("sha-one", "sha-two", "sha-three", "sha-four"))

    await observe(tally, closed=(SECOND,))

    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert is_raised(stored)
    assert stored.bound is not None
    assert stored.bound.observed_value == 2
    assert [event.kind for event in await events_on(port)] == [
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
        RunEventKind.RUN_ALARM_RAISED,
    ]


async def test_a_tick_killed_between_record_and_event_posts_the_event_next_tick(
    monkeypatch,
):
    port = await board()
    tally = supervisor(port)
    posting = port.post_run_event
    attempts: list[str] = []

    async def refuse_once(*, issue_key: str, event):
        attempts.append(issue_key)
        if len(attempts) == 1:
            raise RuntimeError("the tick died between the record and its event")
        return await posting(issue_key=issue_key, event=event)

    monkeypatch.setattr(port, "post_run_event", refuse_once)

    with pytest.raises(RuntimeError):
        await observe(tally)

    # The record landed before the event was attempted, which is why the next
    # tick can tell that the stream still owes one.
    assert len(records_on(port)) == 1
    assert await events_on(port) == []

    await observe(tally)

    assert len(records_on(port)) == 1
    assert [event.kind for event in await events_on(port)] == [
        RunEventKind.RUN_ALARM_RAISED
    ]


async def test_a_healthy_lane_with_no_stored_reading_is_not_written_to():
    port = await board(commits=("sha-one",))
    before = snapshot(port)

    await observe(supervisor(port))

    assert snapshot(port) == before
    assert port.lease_writes == []
    assert records_on(port) == []


async def test_a_finished_lane_with_a_raised_record_is_cleared_and_then_left_alone():
    port = await board()
    tally = supervisor(port)
    await observe(tally)

    # A member owing nothing is observed with no roster and no gap, so a raise
    # standing on a lane that has since finished is cleared rather than kept.
    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=(),
        gap=(),
        criteria=subtree(closed=(FIRST, SECOND)),
    )

    assert [event.kind for event in await events_on(port)] == [
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
    ]
    after = snapshot(port)

    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=(),
        gap=(),
        criteria=subtree(closed=(FIRST, SECOND)),
    )

    assert snapshot(port) == after


async def test_a_lane_with_no_run_state_record_is_passed_over():
    port = FakeTrackerPort(
        issues=[make_tracker_issue(LANE), *subtree()], marker_prefixes=PREFIXES
    )
    before = snapshot(port)

    await observe(supervisor(port))

    assert snapshot(port) == before


def test_a_blank_holder_refuses_before_any_read():
    with pytest.raises(ValueError, match="names the holder"):
        TallySupervisor(
            tracker=FakeTrackerPort(issues=[], marker_prefixes=PREFIXES),
            records=LaneRecordReader(
                tracker=FakeTrackerPort(issues=[], marker_prefixes=PREFIXES),
                operation=OperationConfig(
                    operation_name="fixture",
                    workspace="fixture",
                    marker_prefixes=PREFIXES,
                ),
            ),
            marker_prefixes=PREFIXES,
            max_commits_without_closure=BOUND,
            holder="   ",
            lease_seconds=LEASE_SECONDS,
        )
