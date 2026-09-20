"""One record and transition-only events, over the in-process tracker double."""

import pytest

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, render_lane_record
from kodezart.domain.run_alarm_record import MARKER_PURPOSE, run_alarm_surface
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.domain.tally_record import is_raised
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.services.tally_supervisor import SIGNAL
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.run_event import RunEventKind
from tests.fakes import FakeTrackerPort, make_tracker_issue
from tests.services.lane_tally_fixtures import (
    HEAD,
    HOLDER,
    LEASE_SECONDS,
    PREFIXES,
    SCOPE,
    alarm_marker,
    allow_foreign_write,
    checks,
    close_criterion,
    declared_set_fixture,
    events_on,
    lane_state,
    operation,
    records_on,
    rewrite_record,
    snapshot,
    still_open,
    subject,
    subtree,
    supervisor,
)

LANE = "LANE-1"
FIRST, SECOND = checks(LANE)
SUBJECT = subject(LANE)


every_write_of_a_tick_is_inside_the_declared_set = declared_set_fixture()


async def one_lane(*, commits=("sha-one", "sha-two"), prefixes=PREFIXES):
    from tests.services.lane_tally_fixtures import board

    return await board(lanes=(LANE,), commits=commits, prefixes=prefixes)


async def observe(tally, *, closed=()):
    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=subtree(LANE, closed=closed),
        gap=still_open(LANE, closed=closed),
        criteria=subtree(LANE, closed=closed),
    )


async def test_a_stall_across_many_ticks_leaves_one_record_and_one_raised_event():
    port = await one_lane()
    tally = supervisor(port)

    await observe(tally)
    after_first = snapshot(port)
    for _ in range(4):
        await observe(tally)
        assert snapshot(port) == after_first

    assert len(records_on(port, LANE)) == 1
    assert [event.kind for event in await events_on(port, LANE)] == [
        RunEventKind.RUN_ALARM_RAISED
    ]
    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert is_raised(stored)
    assert stored.raised_by == HOLDER
    assert stored.raised_at_sha == HEAD


async def test_the_marker_prefix_comes_from_operation_configuration():
    other = {**PREFIXES, "run_alarm": "another-alarm"}
    port = await one_lane(prefixes=other)

    await observe(supervisor(port))

    assert len(records_on(port, LANE)) == 1
    assert records_on(port, LANE)[0].body.startswith("[another-alarm:")


async def test_an_absent_alarm_prefix_refuses_before_any_read(monkeypatch):
    port = await one_lane(
        prefixes={k: v for k, v in PREFIXES.items() if k != "run_alarm"}
    )
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
    port = await one_lane()
    tally = supervisor(port)
    await observe(tally)
    raised = records_on(port, LANE)[0]
    close_criterion(port, SECOND)

    await observe(tally, closed=(SECOND,))

    assert [row.comment_key for row in records_on(port, LANE)] == [raised.comment_key]
    assert [event.kind for event in await events_on(port, LANE)] == [
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
    port = await one_lane()
    tally = supervisor(port)
    await observe(tally)
    close_criterion(port, SECOND)
    await observe(tally, closed=(SECOND,))

    rewrite_record(port, LANE, commits=("sha-one", "sha-two", "sha-three", "sha-four"))

    await observe(tally, closed=(SECOND,))

    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert is_raised(stored)
    assert stored.bound is not None
    assert stored.bound.observed_value == 2
    assert [event.kind for event in await events_on(port, LANE)] == [
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
        RunEventKind.RUN_ALARM_RAISED,
    ]


async def test_a_tick_killed_between_record_and_event_posts_the_event_next_tick(
    monkeypatch,
):
    port = await one_lane()
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
    assert len(records_on(port, LANE)) == 1
    assert await events_on(port, LANE) == []

    await observe(tally)

    assert len(records_on(port, LANE)) == 1
    assert [event.kind for event in await events_on(port, LANE)] == [
        RunEventKind.RUN_ALARM_RAISED
    ]


async def test_a_healthy_lane_with_no_stored_reading_is_not_written_to():
    port = await one_lane(commits=("sha-one",))
    before = snapshot(port)

    await observe(supervisor(port))

    assert snapshot(port) == before
    assert port.lease_writes == []
    assert records_on(port, LANE) == []


async def test_a_lane_that_moved_while_still_owing_writes_a_reading_and_posts_nothing():
    """The third write of the record rule announces nothing, in either direction.

    A lane that closed one criterion and still owes another is written a new
    earlier reading, so the bound is measured from where the lane now stands.
    That write is a reading and not a transition: the stream has never carried
    a raise for this signal, so there is nothing to clear, and the events this
    signal posts are the two transitions alone.
    """
    port = await one_lane(commits=("sha-one",))
    close_criterion(port, SECOND)
    tally = supervisor(port)

    await observe(tally, closed=(SECOND,))

    assert len(records_on(port, LANE)) == 1
    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert not is_raised(stored)
    assert stored.readings[2].value.value == (SECOND,)
    assert await events_on(port, LANE) == []

    after = snapshot(port)
    await observe(tally, closed=(SECOND,))
    assert snapshot(port) == after


async def test_a_finished_lane_with_a_raised_record_is_cleared_and_then_left_alone():
    port = await one_lane()
    tally = supervisor(port)
    await observe(tally)
    for key in (FIRST, SECOND):
        close_criterion(port, key)

    # A member owing nothing is observed with no roster and no gap, so a raise
    # standing on a lane that has since finished is cleared rather than kept.
    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=(),
        gap=(),
        criteria=subtree(LANE, closed=(FIRST, SECOND)),
    )

    assert [event.kind for event in await events_on(port, LANE)] == [
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
    ]
    after = snapshot(port)

    await tally.observe(
        scope_key=SCOPE,
        lane_key=LANE,
        roster=(),
        gap=(),
        criteria=subtree(LANE, closed=(FIRST, SECOND)),
    )

    assert snapshot(port) == after


async def test_a_lane_with_no_run_state_record_is_passed_over():
    port = FakeTrackerPort(
        issues=[make_tracker_issue(LANE), *subtree(LANE)], marker_prefixes=PREFIXES
    )
    before = snapshot(port)

    await observe(supervisor(port))

    assert snapshot(port) == before


async def test_the_supervisor_leases_exactly_the_alarm_marker_on_the_lane_issue():
    port = await one_lane()
    tally = supervisor(port)
    expected = run_alarm_surface(issue_key=LANE, marker=alarm_marker(port, LANE))

    await observe(tally)
    close_criterion(port, SECOND)
    await observe(tally, closed=(SECOND,))

    leased = [lease for lease in port.lease_writes if lease.holder == HOLDER]
    assert leased, "a leased write with no lease states nothing about its surface"
    assert [lease.surfaces for lease in leased] == [frozenset({expected})] * len(leased)


async def test_a_lane_fire_holding_the_record_marker_and_the_supervisor_both_write():
    """Two holders, two surfaces, one issue: neither excludes the other."""
    port = await one_lane()
    record_marker = compose_comment_marker(
        prefixes=port.marker_prefixes, purpose=RUN_STATE_PURPOSE, lane=LANE
    )
    fire_surface = run_alarm_surface(issue_key=LANE, marker=record_marker)
    # The fire's own rewrite of the lane record is the one write on this board
    # that the supervisor's declared set does not cover, so it is named here
    # rather than admitted by widening the set.
    allow_foreign_write(port, lane=LANE, marker=record_marker)

    async with RunSurfaceLease(
        tracker=port,
        job_id="fire/lane-one",
        surfaces=frozenset({fire_surface}),
        lease_seconds=LEASE_SECONDS,
    ):
        await observe(supervisor(port))
        rewritten = render_lane_record(
            record=lane_state(LANE, commits=("sha-one", "sha-two", "sha-three")),
            marker_prefixes=PREFIXES,
        )
        await port.upsert_comment(
            target=LANE,
            marker=record_marker,
            body=rewritten.split("\n", 1)[1],
            holder="fire/lane-one",
        )

    assert len(records_on(port, LANE)) == 1
    assert {lease.holder for lease in port.lease_writes} == {HOLDER, "fire/lane-one"}
    _, state = await LaneRecordReader(
        tracker=port,
        operation=operation(),
    ).read(issue_key=LANE, lane_key=LANE)
    assert len(state.commits) == 3


async def test_the_alarm_marker_shares_no_prefix_with_any_other_configured_purpose():
    port = await one_lane()
    marker = alarm_marker(port, LANE)

    for purpose, prefix in port.marker_prefixes.items():
        if purpose == MARKER_PURPOSE:
            continue
        assert not marker.startswith(f"[{prefix}")


async def test_a_tick_in_which_an_alarm_fires_moves_no_state_and_posts_no_halt():
    """The observation is a reading; the board's states are the walk's business."""
    port = await one_lane()
    tally = supervisor(port)
    states = {key: (row.state_name, row.state_kind) for key, row in port.issues.items()}
    changes = dict(port.issue_state_changes)

    await observe(tally)

    stored = await port.read_run_alarm(issue_key=LANE, subject=SUBJECT, signal=SIGNAL)
    assert stored is not None
    assert is_raised(stored), "a tick that raised nothing states nothing about moving"

    assert port.workflow_writes == []
    assert port.restored_states == []
    assert port.issue_writes == []
    assert port.queue_writes == []
    assert port.classification_writes == []
    assert port.claim_writes == []
    assert port.issue_state_changes == changes
    assert {
        key: (row.state_name, row.state_kind) for key, row in port.issues.items()
    } == states

    prefix = f"[{port.marker_prefixes[RUN_EVENT_PURPOSE]}:"
    posted = [row for row in port.comments if row.body.startswith(prefix)]
    streams = [
        event
        for key in port.issues
        for event in await port.lane_run_events(issue_key=key, lane_key=key)
    ]
    assert len(streams) == len(posted)
    assert {event.kind for event in streams} <= {
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
    }
