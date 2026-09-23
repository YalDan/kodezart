"""One tick's containment: a lane's failure is the lane's, and the tick says so."""

import asyncio

import pytest
import structlog.testing

from kodezart.domain.lane_alarms import stored_alarm
from kodezart.domain.run_alarm_record import run_alarm_marker
from kodezart.domain.run_alarm_table import alarm_raised
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.domain.run_shape import TICKET_MARKER_SOURCE, tally_unmoved
from kodezart.domain.stream_signals import lapse_undischarged
from kodezart.services.alarm_supervisor import AlarmSupervisor
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.supervisor_pass import (
    SupervisorIncompleteError,
    SupervisorPass,
)
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.run_alarm import AlarmSignal, CriterionSubject
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.topology import BlockedIssue
from kodezart.types.domain.tracker import IssuePriority
from tests.domain.test_scope_tally import SUBJECT as SCOPE_STALL
from tests.domain.test_scope_tally import inputs as scope_inputs
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort, make_tracker_issue
from tests.services.lane_tally_fixtures import (
    BOUND,
    HEAD,
    HOLDER,
    LEASE_SECONDS,
    PREFIXES,
    SCOPE,
    alarm_marker,
    allow_foreign_write,
    board,
    checks,
    criterion,
    declared_set_fixture,
    events_on,
    operation,
    records_on,
    still_open,
    stored_on,
    subtree,
    supervisor,
)

REF = ScopeRef(kind=ScopeKind.PROJECT, key=SCOPE)
OTHER = ScopeRef(kind=ScopeKind.PROJECT, key="another-project")
LANES = ("LANE-B", "LANE-C")

every_write_of_a_tick_is_inside_the_declared_set = declared_set_fixture()


def ready_set(
    *, ref=REF, lanes=LANES, closed=(), blocked=(), unapproved=(), descendants=()
):
    """One scope reading in the shape the walker's own read composes it in.

    *descendants* are criterion rows under a member's deliverable child. The
    walker's read carries them among the scope's criteria like any other, so
    they are appended to the members' own.
    """
    return ScopeReadySet(
        scope=ResolvedScope(ref=ref, issues=()),
        ready=tuple(
            ScopeReadyLane(
                issue=make_tracker_issue(lane),
                effective_priority=IssuePriority.NONE,
                gap=still_open(lane),
                criteria=subtree(lane),
            )
            for lane in lanes
        ),
        blocked=blocked,
        # Every criterion of the scope, a blocked or unapproved member's
        # included, as the walker's own read carries them.
        criteria=tuple(
            row
            for lane in (
                *lanes,
                *closed,
                *(member.issue_key for member in blocked),
                *unapproved,
            )
            for row in subtree(lane)
        )
        + tuple(descendants),
        closed=tuple(make_tracker_issue(lane) for lane in closed),
        unapproved=unapproved,
    )


def pass_over(port, *, readings, tally=None, scope_arm=None):
    """A tick whose scope reads come from *readings*, keyed by scope reference.

    *scope_arm* answers each scope's stage-barrier observation the same way,
    by scope reference; a scope it does not name observes no stall.
    """

    async def observe_scope(ref):
        answer = (scope_arm or {}).get(ref, ())
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def read_ready(ref):
        answer = readings[ref]
        # Any raisable answer is raised, a stop included: a double that could
        # only fail the way the tick contains failures could never show a stop
        # coming back out of the scope read.
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return SupervisorPass(
        scopes=tuple(readings),
        read_ready=read_ready,
        observe_scope=observe_scope,
        alarms=tally if tally is not None else supervisor(port),
    )


def damage(port, lane):
    """Leave a body at the lane's alarm address that no reader can accept."""
    port.comments.append(
        port.comments[0].model_copy(
            update={
                "comment_key": f"damaged-{lane}",
                "issue_key": lane,
                "body": f"{alarm_marker(port, lane)}\nnot the record's framing at all",
            }
        )
    )


async def test_one_unobservable_lane_is_reported_and_the_others_are_still_observed():
    port = await board(lanes=LANES)
    damage(port, "LANE-B")

    with pytest.raises(SupervisorIncompleteError) as caught:
        await pass_over(port, readings={REF: ready_set()}).run(FIXTURE_EPOCH)

    assert caught.value.failed == ("LANE-B",)
    stored = await stored_on(port, "LANE-C")
    assert stored is not None
    assert alarm_raised(stored)
    assert [event.kind.value for event in await events_on(port, "LANE-C")] == [
        "run_alarm_raised"
    ]


async def test_a_failed_scope_read_does_not_stop_the_next_scope():
    port = await board(lanes=LANES)
    readings = {OTHER: RuntimeError("the scope read failed"), REF: ready_set()}

    with pytest.raises(SupervisorIncompleteError) as caught:
        await pass_over(port, readings=readings).run(FIXTURE_EPOCH)

    assert caught.value.failed == (OTHER.key,)
    assert len(records_on(port, "LANE-B")) == 1
    assert len(records_on(port, "LANE-C")) == 1


async def test_a_failed_scope_arm_is_reported_and_its_lanes_are_still_observed():
    """The scope's own observation failing is the scope's, not its lanes'."""
    port = await board(lanes=LANES)

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(SupervisorIncompleteError) as caught:
            await pass_over(
                port,
                readings={REF: ready_set()},
                scope_arm={REF: RuntimeError("the roster read failed")},
            ).run(FIXTURE_EPOCH)

    assert caught.value.failed == (REF.key,)
    assert [
        (entry["event"], entry["scope"])
        for entry in logs
        if entry["event"] == "supervisor_scope_arm_failed"
    ] == [("supervisor_scope_arm_failed", REF.key)]
    for lane in LANES:
        assert len(records_on(port, lane)) == 1, lane


async def test_a_raised_scope_alarm_is_logged_at_warning_and_the_tick_ran():
    """A scope's stall is said where an operator reads it, and written nowhere.

    Nothing about it is keyed to the scope on a stream or a record: the next
    tick reads the roster and the markers again and says it again.
    """
    port = await board(lanes=LANES)
    stalled = tally_unmoved(
        subject=SCOPE_STALL,
        readings=scope_inputs(),
        raised_at_sha="supervisor",
        raised_by=HOLDER,
    )
    assert stalled is not None

    with structlog.testing.capture_logs() as logs:
        outcome = await pass_over(
            port, readings={REF: ready_set()}, scope_arm={REF: (stalled,)}
        ).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    assert [
        (entry["log_level"], entry["scope"], entry["signal"], entry["marker"])
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ] == [("warning", REF.key, AlarmSignal.TALLY_UNMOVED.value, TICKET_MARKER_SOURCE)]
    # Every comment on the board is on a lane: none carries the scope's stall.
    assert {row.issue_key for row in port.comments} == set(LANES)
    for lane in LANES:
        assert len(records_on(port, lane)) == 1, lane


async def test_every_scopes_barrier_is_observed_and_each_raise_is_logged():
    """Two declared scopes, each with a stalled barrier: two warnings, one each."""
    port = await board(lanes=LANES)
    stalled = tally_unmoved(
        subject=SCOPE_STALL,
        readings=scope_inputs(),
        raised_at_sha="supervisor",
        raised_by=HOLDER,
    )
    assert stalled is not None

    with structlog.testing.capture_logs() as logs:
        outcome = await pass_over(
            port,
            readings={REF: ready_set(), OTHER: ready_set(ref=OTHER, lanes=())},
            scope_arm={REF: (stalled,), OTHER: (stalled,)},
        ).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN
    assert [
        entry["scope"]
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ] == [REF.key, OTHER.key]


async def test_a_failed_ready_read_does_not_hide_the_scopes_barrier():
    """The barrier is observed before the ready read, so its failure hides nothing.

    The scope's ready read fails and its barrier is stalled: the stall is
    still logged for the scope, and the scope is reported once, for the read.
    """
    port = await board(lanes=LANES)
    stalled = tally_unmoved(
        subject=SCOPE_STALL,
        readings=scope_inputs(),
        raised_at_sha="supervisor",
        raised_by=HOLDER,
    )
    assert stalled is not None

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(SupervisorIncompleteError) as caught:
            await pass_over(
                port,
                readings={REF: RuntimeError("the ready read failed")},
                scope_arm={REF: (stalled,)},
            ).run(FIXTURE_EPOCH)

    assert caught.value.failed == (REF.key,)
    assert [
        (entry["scope"], entry["marker"])
        for entry in logs
        if entry["event"] == "supervisor_scope_alarm_raised"
    ] == [(REF.key, TICKET_MARKER_SOURCE)]


async def test_a_whole_tick_over_observable_lanes_reports_that_it_ran():
    port = await board(lanes=LANES)

    outcome = await pass_over(port, readings={REF: ready_set()}).run(FIXTURE_EPOCH)

    assert outcome is PassRun.RAN


async def test_a_finished_member_is_observed_so_a_standing_raise_is_cleared():
    port = await board(lanes=LANES)
    tally = supervisor(port)
    await pass_over(port, readings={REF: ready_set()}, tally=tally).run(FIXTURE_EPOCH)

    # The same board read again with LANE-B finished: it is no longer a ready
    # lane, and the reading that reports it is the one that clears its alarm.
    await pass_over(
        port,
        readings={REF: ready_set(lanes=("LANE-C",), closed=("LANE-B",))},
        tally=tally,
    ).run(FIXTURE_EPOCH)

    assert [event.kind.value for event in await events_on(port, "LANE-B")] == [
        "run_alarm_raised",
        "run_alarm_cleared",
    ]
    stored = await stored_on(port, "LANE-B")
    assert stored is not None
    assert not alarm_raised(stored)
    # What the lane closed is read off the SCOPE's criteria: a finished member is
    # observed with no roster at all, so a tick handing the lane's own roster on
    # in their place would leave a cleared record saying it closed nothing.
    assert stored.readings[2].value.value == tuple(sorted(checks("LANE-B")))


@pytest.mark.parametrize(
    "waiting",
    [
        {"blocked": (BlockedIssue(issue_key="LANE-B", blocker_keys=("LANE-X",)),)},
        {"unapproved": ("LANE-B",)},
    ],
    ids=["blocked", "unapproved"],
)
async def test_a_blocked_members_tally_raise_stands(monkeypatch, waiting):
    """A member nothing can fire has no clock to measure, and is still read.

    Blocked and unapproved are the two waiting members, and each is carried in
    its own part of the reading, so each is asked the same question here.

    Its tally is not composed at all, which is the only safe reading: with no
    roster and no gap it would look like a lane that had finished its work,
    and the standing raise on it would be cleared by the very fact that it is
    stuck. The stated consequence is that the raise keeps standing until the
    member is ready again.

    What its stream already said is read all the same. A criterion whose
    grading lapsed keeps its lane's gap open, so a lane holding one is never a
    ready lane, and a tick that passed over the blocked members could not see
    an undischarged lapse anywhere.
    """
    port = await board(lanes=LANES)
    tally = supervisor(port)
    await pass_over(port, readings={REF: ready_set()}, tally=tally).run(FIXTURE_EPOCH)
    raised = records_on(port, "LANE-B")
    events = await events_on(port, "LANE-B")

    listed: list[str] = []
    listing = port.list_comments

    async def counted(*, issue_key: str):
        listed.append(issue_key)
        return await listing(issue_key=issue_key)

    monkeypatch.setattr(port, "list_comments", counted)

    await pass_over(
        port,
        readings={REF: ready_set(lanes=("LANE-C",), **waiting)},
        tally=tally,
    ).run(FIXTURE_EPOCH)

    assert "LANE-B" in listed
    assert [row.body for row in records_on(port, "LANE-B")] == [
        row.body for row in raised
    ]
    stored = await stored_on(port, "LANE-B")
    assert stored is not None
    assert alarm_raised(stored)
    assert await events_on(port, "LANE-B") == events
    assert [event.kind.value for event in events] == ["run_alarm_raised"]


async def test_a_lapse_on_a_blocked_lane_is_raised_at_its_criterion_until_it_is_ready():
    """A lapse nothing will re-derive is an alarm; the lane made ready clears it.

    The lane finished its first criterion and then found that grading lapsed,
    and said both on its stream; the criterion stands in Todo. Blocked, the
    lane is not going to be run, so the lapse is owed by nobody and the tick
    records it at the criterion's own address on the lane. Nothing is posted:
    a criterion record is not a lane transition. Made ready, the lane will
    grade the criterion again, so the next tick rewrites that address quiet.
    """
    port = await board(lanes=LANES)
    lapsed = checks("LANE-B")[0]
    for kind in (RunEventKind.ISSUE_CROSSED_OFF, RunEventKind.CRITERION_LAPSED):
        await port.post_run_event(
            issue_key="LANE-B",
            event=LaneRunEvent(
                kind=kind, lane_key="LANE-B", subject_key=lapsed, graded_sha=HEAD
            ),
        )
    accounts = await events_on(port, "LANE-B")
    address = CriterionSubject(
        scope_key=SCOPE, issue_id="LANE-B", member_id=lapsed, lane_key="LANE-B"
    )
    tally = supervisor(port)

    await pass_over(
        port,
        readings={
            REF: ready_set(
                lanes=("LANE-C",),
                blocked=(BlockedIssue(issue_key="LANE-B", blocker_keys=("LANE-X",)),),
            )
        },
        tally=tally,
    ).run(FIXTURE_EPOCH)

    records = await port.read_run_alarms(issue_key="LANE-B")
    assert [(row.subject, row.signal) for row in records] == [
        (address, AlarmSignal.LAPSE_UNDISCHARGED)
    ]
    assert records[0].bound is None
    assert await events_on(port, "LANE-B") == accounts

    await pass_over(port, readings={REF: ready_set()}, tally=tally).run(FIXTURE_EPOCH)

    cleared = stored_alarm(
        await port.read_run_alarms(issue_key="LANE-B"),
        subject=address,
        signal=AlarmSignal.LAPSE_UNDISCHARGED,
    )
    assert cleared is not None
    assert cleared.bound is None
    assert (
        lapse_undischarged(
            subject=cleared.subject,
            readings=cleared.readings,
            raised_at_sha=cleared.raised_at_sha,
            raised_by=cleared.raised_by,
        )
        is None
    )
    # The lane's own tally is observed now that it is ready, and announced;
    # the criterion address never is.
    assert [event.kind for event in await events_on(port, "LANE-B")] == [
        *(event.kind for event in accounts),
        RunEventKind.RUN_ALARM_RAISED,
    ]


async def test_a_lapse_on_an_unapproved_lane_is_raised_at_its_criterion():
    """Unapproved is the other waiting member: nothing runs it, so nobody owes it.

    The twin of the blocked case above with the lane carried as unapproved.
    The lane finished its first criterion and found that grading lapsed; the
    criterion stands in Todo, and the tick records the lapse at the
    criterion's own address on the lane and posts nothing.
    """
    port = await board(lanes=LANES)
    lapsed = checks("LANE-B")[0]
    for kind in (RunEventKind.ISSUE_CROSSED_OFF, RunEventKind.CRITERION_LAPSED):
        await port.post_run_event(
            issue_key="LANE-B",
            event=LaneRunEvent(
                kind=kind, lane_key="LANE-B", subject_key=lapsed, graded_sha=HEAD
            ),
        )
    accounts = await events_on(port, "LANE-B")

    await pass_over(
        port,
        readings={REF: ready_set(lanes=("LANE-C",), unapproved=("LANE-B",))},
    ).run(FIXTURE_EPOCH)

    records = await port.read_run_alarms(issue_key="LANE-B")
    assert [(row.subject, row.signal) for row in records] == [
        (
            CriterionSubject(
                scope_key=SCOPE, issue_id="LANE-B", member_id=lapsed, lane_key="LANE-B"
            ),
            AlarmSignal.LAPSE_UNDISCHARGED,
        )
    ]
    assert records[0].bound is None
    assert await events_on(port, "LANE-B") == accounts


async def test_a_lapse_under_a_waiting_lanes_deliverable_child_is_raised_at_it():
    """The scope's criteria reach below the lane's direct children, and are read.

    The lane's lapsed criterion sits under a deliverable child of the lane, and
    the scope's reading carries it among its criteria as the walker's read
    does. The lane is blocked, so nothing re-derives it: the tick records the
    lapse on the lane that announced it, keyed to the child it sits under.
    """
    port = await board(lanes=LANES)
    child = "LANE-B/deliverable"
    lapsed = f"{child}/check"
    for kind in (RunEventKind.ISSUE_CROSSED_OFF, RunEventKind.CRITERION_LAPSED):
        await port.post_run_event(
            issue_key="LANE-B",
            event=LaneRunEvent(
                kind=kind, lane_key="LANE-B", subject_key=lapsed, graded_sha=HEAD
            ),
        )
    address = CriterionSubject(
        scope_key=SCOPE, issue_id=child, member_id=lapsed, lane_key="LANE-B"
    )
    # The declared set enumerates each lane's direct criteria; this address is
    # the supervisor's own, one level further down, and is named here.
    allow_foreign_write(
        port,
        lane="LANE-B",
        marker=run_alarm_marker(
            subject=address,
            signal=AlarmSignal.LAPSE_UNDISCHARGED,
            marker_prefixes=port.marker_prefixes,
        ),
    )

    await pass_over(
        port,
        readings={
            REF: ready_set(
                lanes=("LANE-C",),
                blocked=(BlockedIssue(issue_key="LANE-B", blocker_keys=("LANE-X",)),),
                descendants=(criterion(lapsed, lane=child),),
            )
        },
    ).run(FIXTURE_EPOCH)

    records = await port.read_run_alarms(issue_key="LANE-B")
    assert [(row.subject, row.signal) for row in records] == [
        (address, AlarmSignal.LAPSE_UNDISCHARGED)
    ]
    assert alarm_raised(records[0])


async def test_a_criterion_under_two_member_lanes_is_read_by_each_lanes_own_word():
    """Nested member lanes each read their own stream, and never each other's.

    LANE-B/child is a member whose parent LANE-B is a member too, and both are
    ready. Both graded the child's criterion and crossed it off; the child then
    took it back as a lapse, and it stands in Todo. The child's last word is
    the lapse, and a ready lane discharges it, so the child writes nothing
    about it. LANE-B's last word is still the crossing-off, so LANE-B raises a
    regression for the take-back the child announced: the stated limit.
    """
    outer, inner = "LANE-B", "LANE-B/child"
    port = await board(lanes=(outer, inner))
    shared = checks(inner)[0]
    said = {
        outer: (RunEventKind.ISSUE_CROSSED_OFF,),
        inner: (RunEventKind.ISSUE_CROSSED_OFF, RunEventKind.CRITERION_LAPSED),
    }
    for lane, kinds in said.items():
        for kind in kinds:
            await port.post_run_event(
                issue_key=lane,
                event=LaneRunEvent(
                    kind=kind, lane_key=lane, subject_key=shared, graded_sha=HEAD
                ),
            )
    through_outer = CriterionSubject(
        scope_key=SCOPE, issue_id=inner, member_id=shared, lane_key=outer
    )
    # The criterion sits under the inner lane, so the outer lane's address for
    # it is the supervisor's own write outside the outer lane's direct set.
    allow_foreign_write(
        port,
        lane=outer,
        marker=run_alarm_marker(
            subject=through_outer,
            signal=AlarmSignal.TALLY_REGRESSED,
            marker_prefixes=port.marker_prefixes,
        ),
    )

    await pass_over(port, readings={REF: ready_set(lanes=(outer, inner))}).run(
        FIXTURE_EPOCH
    )

    def criterion_records(rows):
        return [
            (row.subject, row.signal, alarm_raised(row))
            for row in rows
            if isinstance(row.subject, CriterionSubject)
        ]

    assert criterion_records(await port.read_run_alarms(issue_key=outer)) == [
        (through_outer, AlarmSignal.TALLY_REGRESSED, True)
    ]
    assert criterion_records(await port.read_run_alarms(issue_key=inner)) == []


@pytest.mark.parametrize("stopped", ["the lane observation", "the scope read"])
async def test_cancellation_is_not_swallowed(stopped):
    """Neither boundary contains the caller's stop, only a lane's own failure.

    The tick draws two: one around a lane's observation and one around a
    scope's read. A stop is not the failure of the lane or the scope it
    arrived in, so it comes straight back out of the tick — nothing is written
    down as having failed and no scope is counted as unobserved.
    """
    port = await board(lanes=LANES)

    class Cancelling(AlarmSupervisor):
        async def observe_lane(self, **_):
            raise asyncio.CancelledError

    if stopped == "the scope read":
        readings = {REF: asyncio.CancelledError()}
        tally = supervisor(port)
    else:
        readings = {REF: ready_set()}
        tally = Cancelling(
            tracker=port,
            records=LaneRecordReader(tracker=port, operation=operation()),
            marker_prefixes=PREFIXES,
            max_commits_without_closure=BOUND,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
        )

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(asyncio.CancelledError):
            await pass_over(port, readings=readings, tally=tally).run(FIXTURE_EPOCH)

    assert [
        entry
        for entry in logs
        if entry["event"] in {"supervisor_scope_failed", "supervisor_lane_failed"}
    ] == []


def test_a_blank_holder_refuses_before_any_read():
    port = FakeTrackerPort(issues=[], marker_prefixes=PREFIXES)

    with pytest.raises(ValueError, match="names the holder"):
        AlarmSupervisor(
            tracker=port,
            records=LaneRecordReader(tracker=port, operation=operation()),
            marker_prefixes=PREFIXES,
            max_commits_without_closure=BOUND,
            holder="   ",
            lease_seconds=LEASE_SECONDS,
        )
