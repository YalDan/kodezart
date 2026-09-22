"""What one lane's observation leaves at each of its addresses.

The composition is pure: the lane's run state, the records already on its
carrier and its event stream go in, and the records to write come out. Each
case writes out the one fact it turns on; everything else is the same lane
with the same two criteria.
"""

import pytest

from kodezart.domain.lane_alarms import (
    OBSERVED_ALARMS,
    Finished,
    Ready,
    Waiting,
    alarm_event_due,
    lane_alarm_records,
    next_alarm_record,
    stored_alarm,
)
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    CriterionSubject,
    LaneSubject,
)
from kodezart.types.domain.run_event import RunEventKind
from tests.domain.test_tally_record import (
    FIRST,
    HEAD,
    HOLDER,
    LANE,
    SCOPE_KEY,
    SECOND,
    criterion,
    lane_record,
)

BOUND = 1
GRADED = "graded-at"

CROSSED_OFF = RunEventKind.ISSUE_CROSSED_OFF
REFUTED = RunEventKind.CRITERION_REFUTED
LAPSED = RunEventKind.CRITERION_LAPSED


def said(kind, member, *, lane=LANE.lane_key):
    """One account the lane posted about *member* on its own stream."""
    return LaneRunEvent(kind=kind, lane_key=lane, subject_key=member, graded_sha=GRADED)


def compose(*, standing, criteria, events=(), stored=(), commits=()):
    return lane_alarm_records(
        scope_key=SCOPE_KEY,
        lane_key=LANE.lane_key,
        standing=standing,
        criteria=criteria,
        record=lane_record(commits=commits),
        stored=stored,
        events=events,
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )


def at(member, *, parent=LANE.lane_key):
    return CriterionSubject(
        scope_key=SCOPE_KEY,
        issue_id=parent,
        member_id=member,
        lane_key=LANE.lane_key,
    )


#: The first criterion finished by the lane and then moved back to Todo by
#: something else; the second still owed and never accounted for.
MOVED_BACK = (criterion(FIRST), criterion(SECOND))
#: The same two, and the lane's own account of the first.
FINISHED_FIRST = (said(CROSSED_OFF, FIRST),)


def test_the_observation_folds_exactly_the_lane_and_criterion_signals():
    assert OBSERVED_ALARMS == {
        AlarmSignal.TALLY_UNMOVED,
        AlarmSignal.TALLY_REGRESSED,
        AlarmSignal.LAPSE_UNDISCHARGED,
    }


def test_a_quiet_criterion_with_no_stored_record_writes_nothing():
    """A criterion the lane finished and that is still Done: nothing to say.

    Written anyway, a healthy walk would leave one record per finished
    criterion per tick, and the quiet run would not be quiet.
    """
    records = compose(
        standing=Ready(roster=(criterion(FIRST, closed=True),), gap=()),
        criteria=(criterion(FIRST, closed=True),),
        events=FINISHED_FIRST,
    )

    assert records == ()


def test_a_criterion_moved_back_without_an_account_is_one_regression_record():
    records = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
    )

    assert [(r.subject, r.signal) for r in records] == [
        (at(FIRST), AlarmSignal.TALLY_REGRESSED)
    ]
    assert records[0].bound is None
    assert records[0].raised_at_sha == HEAD


def test_a_raise_and_its_clear_rewrite_the_one_record():
    """Once raised, the address says so; once it ends, it says that once."""
    raised = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
    )
    refuted = (*FINISHED_FIRST, said(REFUTED, FIRST))

    cleared = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=refuted,
        stored=raised,
    )

    assert [(r.subject, r.signal) for r in cleared] == [
        (at(FIRST), AlarmSignal.TALLY_REGRESSED)
    ]
    assert next_alarm_record(stored=cleared[0], observed=cleared[0]) is None
    # And having said it, the address says nothing more.
    assert (
        compose(
            standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
            criteria=MOVED_BACK,
            events=refuted,
            stored=cleared,
        )
        == ()
    )


def test_a_standing_raise_is_not_rewritten():
    raised = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
    )

    again = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
        stored=raised,
    )

    assert again == ()


def test_a_waiting_lane_composes_no_tally_record_and_still_reads_its_lapses():
    """Blocked or unapproved: its clock is not measured, its stream is read.

    The same lane read as ready would raise its tally — more commits than the
    bound, nothing closed — so the absence of a tally record is the standing,
    not the board. And the lapse it announced is undischarged because nothing
    is going to run it.
    """
    lapsed = (said(CROSSED_OFF, FIRST), said(LAPSED, FIRST))
    stalled = ("sha-one", "sha-two")
    ready = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=lapsed,
        commits=stalled,
    )
    assert AlarmSignal.TALLY_UNMOVED in {r.signal for r in ready}

    records = compose(
        standing=Waiting(), criteria=MOVED_BACK, events=lapsed, commits=stalled
    )

    assert [(r.subject, r.signal) for r in records] == [
        (at(FIRST), AlarmSignal.LAPSE_UNDISCHARGED)
    ]


def test_the_same_lapse_on_a_ready_lane_writes_nothing():
    records = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=(said(CROSSED_OFF, FIRST), said(LAPSED, FIRST)),
    )

    assert records == ()


def test_a_lapse_under_a_deliverable_child_is_keyed_to_its_parent_and_its_lane():
    under = criterion(FIRST, parent="LANE-1/deliverable")

    records = compose(
        standing=Waiting(),
        criteria=(under, criterion(SECOND)),
        events=(said(LAPSED, FIRST),),
    )

    assert [r.subject for r in records] == [at(FIRST, parent="LANE-1/deliverable")]


def test_a_finished_lane_is_read_with_no_roster_and_no_gap():
    """A finished lane with a raised tally is cleared, as it was before."""
    stalled = ("sha-one", "sha-two")
    raised = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        commits=stalled,
    )
    [tally] = raised
    done = (criterion(FIRST, closed=True), criterion(SECOND, closed=True))

    cleared = compose(
        standing=Finished(), criteria=done, stored=raised, commits=stalled
    )

    assert [(r.subject, r.signal) for r in cleared] == [
        (tally.subject, AlarmSignal.TALLY_UNMOVED)
    ]
    assert cleared[0].bound is None


def test_a_criterion_the_scope_read_does_not_carry_is_skipped():
    """Its state cannot be read, so nothing is written and nothing refuses."""
    records = compose(
        standing=Waiting(),
        criteria=(criterion(SECOND),),
        events=(said(LAPSED, FIRST),),
    )

    assert records == ()


def test_an_entry_keyed_to_a_criterion_that_is_not_an_account_observes_nothing():
    """Membership is read off the lane's ACCOUNTS, not off any keyed entry.

    A lane's stream carries other kinds keyed to a member; only crossing a
    criterion off, refuting it or finding its grading lapsed says the lane
    graded it, so nothing else puts a criterion under this lane's watch.
    """
    records = compose(
        standing=Waiting(),
        criteria=MOVED_BACK,
        events=(said(RunEventKind.RUN_ALARM_RAISED, FIRST),),
    )

    assert records == ()


def test_stored_alarm_reads_one_address_out_of_the_listing():
    regressed = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
    )

    assert (
        stored_alarm(regressed, subject=at(FIRST), signal=AlarmSignal.TALLY_REGRESSED)
        == regressed[0]
    )
    assert (
        stored_alarm(
            regressed, subject=at(FIRST), signal=AlarmSignal.LAPSE_UNDISCHARGED
        )
        is None
    )
    assert (
        stored_alarm(regressed, subject=at(SECOND), signal=AlarmSignal.TALLY_REGRESSED)
        is None
    )


def test_only_a_lane_subject_record_owes_an_event():
    """One event per lane transition, never one per criterion."""
    [regression] = compose(
        standing=Ready(roster=MOVED_BACK, gap=MOVED_BACK),
        criteria=MOVED_BACK,
        events=FINISHED_FIRST,
    )
    assert not isinstance(regression.subject, LaneSubject)

    with pytest.raises(ValueError, match="keyed to a lane"):
        alarm_event_due(record=regression, events=())
