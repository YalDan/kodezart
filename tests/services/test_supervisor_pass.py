"""One tick's containment: a lane's failure is the lane's, and the tick says so."""

import asyncio

import pytest
import structlog.testing

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.lane_record import RUN_STATE_PURPOSE
from kodezart.domain.tally_record import is_raised
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_alarm_recorder import RunAlarmRecorder
from kodezart.services.supervisor_pass import (
    SupervisorIncompleteError,
    SupervisorPass,
)
from kodezart.services.tally_supervisor import SIGNAL, TallySupervisor
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.topology import BlockedIssue
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort, make_tracker_issue
from tests.services.lane_tally_fixtures import (
    BOUND,
    LEASE_SECONDS,
    PREFIXES,
    SCOPE,
    ageing,
    alarm_marker,
    board,
    checks,
    declared_set_fixture,
    events_on,
    operation,
    recorder,
    records_on,
    still_open,
    subject,
    subtree,
    supervisor,
)

REF = ScopeRef(kind=ScopeKind.PROJECT, key=SCOPE)
OTHER = ScopeRef(kind=ScopeKind.PROJECT, key="another-project")
LANES = ("LANE-B", "LANE-C")

every_write_of_a_tick_is_inside_the_declared_set = declared_set_fixture()


def ready_set(*, ref=REF, lanes=LANES, closed=(), blocked=()):
    """One scope reading in the shape the walker's own read composes it in."""
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
        criteria=tuple(row for lane in (*lanes, *closed) for row in subtree(lane)),
        closed=tuple(make_tracker_issue(lane) for lane in closed),
    )


def pass_over(port, *, readings, tally=None):
    """A tick whose scope reads come from *readings*, keyed by scope reference."""

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
        tally=tally if tally is not None else supervisor(port),
        ageing=ageing(port),
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
    stored = await port.read_run_alarm(
        issue_key="LANE-C", subject=subject("LANE-C"), signal=SIGNAL
    )
    assert stored is not None
    assert is_raised(stored)
    assert [event.kind.value for event in await events_on(port, "LANE-C")] == [
        "run_alarm_raised"
    ]


async def test_a_damaged_lane_record_leaves_questions_unobserved_and_tallies_written():
    """The scope's position needs every member's record; its tallies do not.

    One member's run-state record is damaged, so the position the scope's
    open questions are aged against cannot be read and none of them is aged
    this tick: the scope is reported. The damaged lane's own tally fails as
    that lane's, and the other lane's tally is still written.
    """
    port = await board(lanes=LANES)
    marker = compose_comment_marker(
        prefixes=PREFIXES, purpose=RUN_STATE_PURPOSE, lane="LANE-B"
    )
    index = next(
        position
        for position, row in enumerate(port.comments)
        if row.issue_key == "LANE-B" and row.body.startswith(marker)
    )
    port.comments[index] = port.comments[index].model_copy(
        update={"body": f"{marker}\nnot a lane record"}
    )

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(SupervisorIncompleteError) as caught:
            await pass_over(port, readings={REF: ready_set()}).run(FIXTURE_EPOCH)

    assert caught.value.failed == (SCOPE, "LANE-B")
    assert [
        entry["scope"]
        for entry in logs
        if entry["event"] == "supervisor_escalations_unobserved"
    ] == [SCOPE]
    assert records_on(port, "LANE-B") == []
    assert len(records_on(port, "LANE-C")) == 1


async def test_a_failed_scope_read_does_not_stop_the_next_scope():
    port = await board(lanes=LANES)
    readings = {OTHER: RuntimeError("the scope read failed"), REF: ready_set()}

    with pytest.raises(SupervisorIncompleteError) as caught:
        await pass_over(port, readings=readings).run(FIXTURE_EPOCH)

    assert caught.value.failed == (OTHER.key,)
    assert len(records_on(port, "LANE-B")) == 1
    assert len(records_on(port, "LANE-C")) == 1


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
    stored = await port.read_run_alarm(
        issue_key="LANE-B", subject=subject("LANE-B"), signal=SIGNAL
    )
    assert stored is not None
    assert not is_raised(stored)
    # What the lane closed is read off the SCOPE's criteria: a finished member is
    # observed with no roster at all, so a tick handing the lane's own roster on
    # in their place would leave a cleared record saying it closed nothing.
    assert stored.readings[2].value.value == tuple(sorted(checks("LANE-B")))


async def test_a_blocked_member_is_not_observed_and_its_raise_stands(monkeypatch):
    """A member nothing can fire records nothing, so there is nothing to measure.

    A blocked member is not observed at all, which is the only safe reading:
    with no roster and no gap it would look like a lane that had finished its
    work, and the standing raise on it would be cleared by the very fact that
    it is stuck. The stated consequence is that the raise keeps standing until
    the member is ready again.

    Its run-state record is still read, once, for the scope's position the
    open questions are aged against (KOD-892): a lane blocked when a question
    was first observed would otherwise count its whole history once it is
    ready again. No alarm address of it is read, which is what observing it
    would begin with.
    """
    port = await board(lanes=LANES)
    tally = supervisor(port)
    await pass_over(port, readings={REF: ready_set()}, tally=tally).run(FIXTURE_EPOCH)

    listed: list[str] = []
    reading = port.read_run_alarm

    async def counted(*, issue_key: str, subject, signal):
        listed.append(issue_key)
        return await reading(issue_key=issue_key, subject=subject, signal=signal)

    monkeypatch.setattr(port, "read_run_alarm", counted)

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

    assert "LANE-B" not in listed
    assert listed, "a tick that read nothing states nothing about what it skipped"
    stored = await port.read_run_alarm(
        issue_key="LANE-B", subject=subject("LANE-B"), signal=SIGNAL
    )
    assert stored is not None
    assert is_raised(stored)
    assert [event.kind.value for event in await events_on(port, "LANE-B")] == [
        "run_alarm_raised"
    ]


@pytest.mark.parametrize("stopped", ["the lane observation", "the scope read"])
async def test_cancellation_is_not_swallowed(stopped):
    """Neither boundary contains the caller's stop, only a lane's own failure.

    The tick draws two: one around a lane's observation and one around a
    scope's read. A stop is not the failure of the lane or the scope it
    arrived in, so it comes straight back out of the tick — nothing is written
    down as having failed and no scope is counted as unobserved.
    """
    port = await board(lanes=LANES)

    class Cancelling(TallySupervisor):
        async def observe(self, **_):
            raise asyncio.CancelledError

    if stopped == "the scope read":
        readings = {REF: asyncio.CancelledError()}
        tally = supervisor(port)
    else:
        readings = {REF: ready_set()}
        tally = Cancelling(
            records=LaneRecordReader(tracker=port, operation=operation()),
            alarms=recorder(port),
            max_commits_without_closure=BOUND,
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
        RunAlarmRecorder(
            tracker=port,
            marker_prefixes=PREFIXES,
            holder="   ",
            lease_seconds=LEASE_SECONDS,
        )
