"""One tick's containment: a lane's failure is the lane's, and the tick says so."""

import asyncio

import pytest

from kodezart.domain.tally_record import is_raised
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.supervisor_pass import (
    SupervisorIncompleteError,
    SupervisorPass,
)
from kodezart.services.tally_supervisor import SIGNAL, TallySupervisor
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ResolvedScope, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort, make_tracker_issue
from tests.services.lane_tally_fixtures import (
    BOUND,
    LEASE_SECONDS,
    PREFIXES,
    SCOPE,
    alarm_marker,
    board,
    events_on,
    operation,
    records_on,
    still_open,
    subject,
    subtree,
    supervisor,
)

REF = ScopeRef(kind=ScopeKind.PROJECT, key=SCOPE)
OTHER = ScopeRef(kind=ScopeKind.PROJECT, key="another-project")
LANES = ("LANE-B", "LANE-C")


def ready_set(*, ref=REF, lanes=LANES, closed=()):
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
        blocked=(),
        criteria=tuple(row for lane in (*lanes, *closed) for row in subtree(lane)),
        closed=tuple(make_tracker_issue(lane) for lane in closed),
    )


def pass_over(port, *, readings, tally=None):
    """A tick whose scope reads come from *readings*, keyed by scope reference."""

    async def read_ready(ref):
        answer = readings[ref]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return SupervisorPass(
        scopes=tuple(readings),
        read_ready=read_ready,
        tally=tally if tally is not None else supervisor(port),
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


async def test_cancellation_is_not_swallowed():
    """The lane boundary contains a lane's failure, never the caller's stop."""
    port = await board(lanes=LANES)

    class Cancelling(TallySupervisor):
        async def observe(self, **_):
            raise asyncio.CancelledError

    cancelling = Cancelling(
        tracker=port,
        records=LaneRecordReader(tracker=port, operation=operation()),
        marker_prefixes=PREFIXES,
        max_commits_without_closure=BOUND,
        holder="kodezart/supervisor",
        lease_seconds=LEASE_SECONDS,
    )

    with pytest.raises(asyncio.CancelledError):
        await pass_over(port, readings={REF: ready_set()}, tally=cancelling).run(
            FIXTURE_EPOCH
        )


def test_a_blank_holder_refuses_before_any_read():
    port = FakeTrackerPort(issues=[], marker_prefixes=PREFIXES)

    with pytest.raises(ValueError, match="names the holder"):
        TallySupervisor(
            tracker=port,
            records=LaneRecordReader(tracker=port, operation=operation()),
            marker_prefixes=PREFIXES,
            max_commits_without_closure=BOUND,
            holder="   ",
            lease_seconds=LEASE_SECONDS,
        )
