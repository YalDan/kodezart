"""A criterion's own state and its lane's account of it, read together.

Every reading here is built by hand from the three things the signals read:
the criterion's workflow kind, the lane's projected stream, and whether the
walk re-derives the lane. The folds are pure, so what a case asserts is the
whole of what the fold answers.
"""

import pytest

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.stream_signals import lapse_undischarged, tally_regressed
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    CriterionSubject,
    LaneSubject,
    PresenceEvidence,
    RunEventProjection,
    RunEventsEvidence,
    StateEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import WorkflowStateKind

SCOPE = "scope-under-watch"
LANE = "LANE-7"
CRITERION = "LANE-7/criterion-a"
OTHER = "LANE-7/criterion-b"
#: A deliverable child of the lane: a criterion under it has this as its
#: parent, and is still graded, and owned, by the lane above it.
DELIVERABLE = "LANE-7/deliverable"
HEAD = "observed-head"
HOLDER = "operation/supervisor"

CROSSED_OFF = RunEventKind.ISSUE_CROSSED_OFF
REFUTED = RunEventKind.CRITERION_REFUTED
LAPSED = RunEventKind.CRITERION_LAPSED


def subject(*, parent=LANE, lane=LANE, member=CRITERION):
    return CriterionSubject(
        scope_key=SCOPE, issue_id=parent, member_id=member, lane_key=lane
    )


def state(kind, *, source=CRITERION):
    return AlarmReading(source_ref=source, value=StateEvidence(value=kind), at_sha=HEAD)


def account(*kinds, source=LANE, member=CRITERION):
    return AlarmReading(
        source_ref=source,
        value=RunEventsEvidence(
            value=tuple(
                RunEventProjection(kind=kind, subject_key=member) for kind in kinds
            )
        ),
        at_sha=HEAD,
    )


def rederived(value, *, source=LANE):
    return AlarmReading(
        source_ref=source, value=PresenceEvidence(value=value), at_sha=HEAD
    )


def regressed(readings, *, on=None):
    return tally_regressed(
        subject=subject() if on is None else on,
        readings=readings,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )


def undischarged(readings, *, on=None):
    return lapse_undischarged(
        subject=subject() if on is None else on,
        readings=readings,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )


#: The firing reading of each signal and its near-identical quiet twin: one
#: fact apart, so the fact is the one the signal turns on. Named here so the
#: signal's own module owns the pair every other test reads.
REGRESSED_PAIR = (
    subject(),
    (state(WorkflowStateKind.UNSTARTED), account(CROSSED_OFF)),
    (state(WorkflowStateKind.UNSTARTED), account(CROSSED_OFF, REFUTED)),
)
LAPSE_PAIR = (
    subject(),
    (
        state(WorkflowStateKind.UNSTARTED),
        account(CROSSED_OFF, LAPSED),
        rederived(False),
    ),
    (state(WorkflowStateKind.UNSTARTED), account(CROSSED_OFF, LAPSED), rederived(True)),
)


def test_a_crossed_off_criterion_back_in_todo_without_a_refutation_regresses():
    on, firing, _ = REGRESSED_PAIR

    alarm = regressed(firing, on=on)

    assert alarm is not None
    assert alarm.signal is AlarmSignal.TALLY_REGRESSED
    assert alarm.subject == on
    assert alarm.readings == firing
    # An identity and a kind, not a count: there is no threshold to report.
    assert alarm.bound is None
    assert (alarm.raised_at_sha, alarm.raised_by) == (HEAD, HOLDER)


def test_the_same_move_followed_by_its_refutation_does_not_regress():
    on, _, clean = REGRESSED_PAIR

    assert regressed(clean, on=on) is None


def test_the_last_account_decides():
    """Crossed off, refuted, crossed off again, and now back in Todo.

    The refutation was the lane's word about the earlier grading; the later
    crossing-off is its word about the current one, and nothing took that
    back. Read by the first account instead, this would stay quiet.
    """
    readings = (
        state(WorkflowStateKind.UNSTARTED),
        account(CROSSED_OFF, REFUTED, CROSSED_OFF),
    )

    alarm = regressed(readings)

    assert alarm is not None
    assert alarm.signal is AlarmSignal.TALLY_REGRESSED


def test_a_lapse_does_not_regress():
    """The lane took the criterion back and said it lapsed: owed, not regressed."""
    readings = (state(WorkflowStateKind.UNSTARTED), account(CROSSED_OFF, LAPSED))

    assert regressed(readings) is None


def test_a_crossed_off_criterion_moved_to_in_review_does_not_regress():
    """Out of Done into review is not back to owed, so it is not the move."""
    readings = (state(WorkflowStateKind.STARTED), account(CROSSED_OFF))

    assert regressed(readings) is None


@pytest.mark.parametrize(
    "kind",
    [
        WorkflowStateKind.COMPLETED,
        WorkflowStateKind.STARTED,
        WorkflowStateKind.BACKLOG,
        WorkflowStateKind.TRIAGE,
        WorkflowStateKind.CANCELED,
        WorkflowStateKind.DUPLICATE,
    ],
)
def test_only_a_move_back_to_todo_is_the_regression(kind):
    assert regressed((state(kind), account(CROSSED_OFF))) is None


def test_a_criterion_its_lane_never_accounted_for_is_quiet_everywhere():
    """Nothing here infers an account from a state."""
    silent = account()

    assert regressed((state(WorkflowStateKind.UNSTARTED), silent)) is None
    assert (
        undischarged((state(WorkflowStateKind.UNSTARTED), silent, rederived(False)))
        is None
    )


def test_a_lapse_on_a_lane_that_is_not_ready_is_undischarged():
    on, firing, _ = LAPSE_PAIR

    alarm = undischarged(firing, on=on)

    assert alarm is not None
    assert alarm.signal is AlarmSignal.LAPSE_UNDISCHARGED
    assert alarm.subject == on
    assert alarm.readings == firing
    assert alarm.bound is None


def test_the_identical_lapse_on_a_ready_lane_raises_nothing():
    on, _, clean = LAPSE_PAIR

    assert undischarged(clean, on=on) is None


def test_a_lapse_the_next_grading_closed_again_owes_nothing():
    readings = (
        state(WorkflowStateKind.COMPLETED),
        account(CROSSED_OFF, LAPSED),
        rederived(False),
    )

    assert undischarged(readings) is None


def test_a_criterion_crossed_off_again_after_its_lapse_is_not_lapsed():
    readings = (
        state(WorkflowStateKind.UNSTARTED),
        account(LAPSED, CROSSED_OFF),
        rederived(False),
    )

    assert undischarged(readings) is None


def test_a_lapse_under_a_deliverable_child_is_owned_by_the_lane_that_announced_it():
    """The criterion's parent is a deliverable child; its owner is the lane.

    The re-derivation reading is sourced at the lane in whose subtree the
    criterion sits, and that is the lane the raise names: a ready lane
    discharges the lapse even though the parent fires nothing, and a lane
    that is not ready raises it keyed to that lane.
    """
    on = subject(parent=DELIVERABLE)
    lapse = (state(WorkflowStateKind.UNSTARTED), account(CROSSED_OFF, LAPSED))

    assert undischarged((*lapse, rederived(True)), on=on) is None
    alarm = undischarged((*lapse, rederived(False)), on=on)
    assert alarm is not None
    assert alarm.subject.lane_key == LANE
    assert alarm.subject.issue_id == DELIVERABLE
    # Asked of the parent instead, the reading names a carrier that was never
    # going to grade anything, and it refuses rather than answer for it.
    with pytest.raises(RunShapeReadError):
        undischarged((*lapse, rederived(False, source=DELIVERABLE)), on=on)


def _misread(name):
    """One reading of the lapse fixture, composed at a wrong address."""
    unstarted = WorkflowStateKind.UNSTARTED
    readings = {
        "state read at the lane": (
            state(unstarted, source=LANE),
            account(LAPSED),
            rederived(False),
        ),
        "account read at another lane": (
            state(unstarted),
            account(LAPSED, source="LANE-8"),
            rederived(False),
        ),
        "account keyed to another criterion": (
            state(unstarted),
            account(LAPSED, member=OTHER),
            rederived(False),
        ),
        "account carrying another kind": (
            state(unstarted),
            account(RunEventKind.RUN_ALARM_RAISED),
            rederived(False),
        ),
        "presence read at another lane": (
            state(unstarted),
            account(LAPSED),
            rederived(False, source="LANE-8"),
        ),
        "another evidence kind": (
            AlarmReading(source_ref=CRITERION, value=CountEvidence(value=1)),
            account(LAPSED),
            rederived(False),
        ),
        "a reading missing": (state(unstarted), account(LAPSED)),
    }
    return readings[name]


@pytest.mark.parametrize(
    "name",
    [
        "state read at the lane",
        "account read at another lane",
        "account keyed to another criterion",
        "account carrying another kind",
        "presence read at another lane",
        "another evidence kind",
        "a reading missing",
    ],
)
def test_readings_about_another_criterion_or_lane_refuse(name):
    with pytest.raises(RunShapeReadError):
        undischarged(_misread(name))
    # The regression reads the first two of the same readings, so each of its
    # own two addresses refuses the same way; the third is the lapse's alone.
    if name not in {"presence read at another lane", "a reading missing"}:
        with pytest.raises(RunShapeReadError):
            regressed(_misread(name)[:2])
    else:
        with pytest.raises(RunShapeReadError):
            regressed(_misread(name)[:1])


@pytest.mark.parametrize(
    "on",
    [
        LaneSubject(scope_key=SCOPE, lane_key=LANE),
        CriterionSubject(scope_key=SCOPE, issue_id=LANE, member_id=CRITERION),
    ],
    ids=["a lane subject", "a criterion with no observing lane"],
)
def test_a_subject_naming_no_observing_lane_refuses(on):
    _, regress, _ = REGRESSED_PAIR
    _, lapse, _ = LAPSE_PAIR

    with pytest.raises(RunShapeReadError):
        regressed(regress, on=on)
    with pytest.raises(RunShapeReadError):
        undischarged(lapse, on=on)


@pytest.mark.parametrize(
    ("fold", "pair"),
    [(tally_regressed, REGRESSED_PAIR), (lapse_undischarged, LAPSE_PAIR)],
    ids=["tally_regressed", "lapse_undischarged"],
)
def test_a_raise_replays_to_itself_from_its_own_readings(fold, pair):
    on, firing, _ = pair
    alarm = fold(subject=on, readings=firing, raised_at_sha=HEAD, raised_by=HOLDER)
    assert alarm is not None

    replayed = fold(
        subject=alarm.subject,
        readings=alarm.readings,
        raised_at_sha=alarm.raised_at_sha,
        raised_by=alarm.raised_by,
    )

    assert replayed == alarm
