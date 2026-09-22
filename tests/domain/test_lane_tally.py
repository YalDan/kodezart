"""The lane arm of one signal: the same tally read twice, over its own commits."""

import pytest

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_alarm_table import alarm_raised
from kodezart.domain.run_shape import COMMITS_WITHOUT_CLOSURE_BOUND, tally_unmoved
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    IssueSubject,
    LaneSubject,
    LaneTally,
    ReferencesEvidence,
    RunAlarm,
    TallyEvidence,
    TextEvidence,
)
from tests.domain.test_scope_tally import SUBJECT as SCOPE_SUBJECT
from tests.domain.test_scope_tally import inputs as scope_inputs

SCOPE_KEY = "scoped-project"
LANE = LaneSubject(scope_key=SCOPE_KEY, lane_key="LANE-1")
HEAD = "head-sha"
HOLDER = "kodezart/supervisor"


def tally(open_keys=("c-one", "c-two"), commits=()):
    return LaneTally(open=tuple(open_keys), commits=tuple(commits))


def readings(*, anchor=None, latest=None, closed=(), bound=1):
    """The arm's four readings in order, all sourced where the arm expects them."""
    return (
        AlarmReading(
            source_ref=LANE.lane_key,
            value=TallyEvidence(value=tally() if anchor is None else anchor),
        ),
        AlarmReading(
            source_ref=LANE.lane_key,
            value=TallyEvidence(value=tally() if latest is None else latest),
        ),
        AlarmReading(
            source_ref=LANE.lane_key, value=ReferencesEvidence(value=tuple(closed))
        ),
        AlarmReading(
            source_ref=COMMITS_WITHOUT_CLOSURE_BOUND, value=CountEvidence(value=bound)
        ),
    )


def observe(values, subject=LANE):
    return tally_unmoved(
        subject=subject, readings=values, raised_at_sha=HEAD, raised_by=HOLDER
    )


def firing(*, bound=1):
    return readings(
        anchor=tally(commits=()),
        latest=tally(commits=("sha-one", "sha-two")),
        closed=(),
        bound=bound,
    )


def test_work_past_the_bound_with_nothing_closed_raises():
    values = firing()

    alarm = observe(values)

    assert alarm is not None
    assert alarm.subject == LANE
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert alarm.readings == values
    assert alarm.raised_at_sha == HEAD
    assert alarm.raised_by == HOLDER
    assert alarm.bound is not None
    assert alarm.bound.config_field == COMMITS_WITHOUT_CLOSURE_BOUND
    assert alarm.bound.configured_value == 1
    assert alarm.bound.observed_value == 2


def test_the_same_readings_with_one_closed_criterion_are_quiet():
    """The only difference from the firing fixture is that something closed."""
    assert (
        observe(
            readings(
                anchor=tally(("c-one", "c-two")),
                latest=tally(("c-one",), commits=("sha-one", "sha-two")),
                closed=("c-two",),
            )
        )
        is None
    )


def test_exactly_the_bound_is_quiet():
    assert observe(firing(bound=2)) is None


def test_a_lane_owing_nothing_is_quiet():
    assert (
        observe(
            readings(
                anchor=tally(("c-one",)),
                latest=tally((), commits=("sha-one", "sha-two")),
                bound=0,
            )
        )
        is None
    )


def test_work_since_counts_identities_not_lengths():
    """Two readings of a commit series, not the difference of two counts."""
    alarm = observe(
        readings(
            anchor=tally(commits=("sha-a", "sha-b")),
            latest=tally(commits=("sha-a", "sha-c", "sha-d")),
        )
    )

    assert alarm is not None
    assert alarm.bound is not None
    assert alarm.bound.observed_value == 2


def test_a_raised_alarm_replays_to_itself_and_a_quiet_record_replays_to_nothing():
    alarm = observe(firing())
    assert alarm is not None
    assert observe(alarm.readings) == alarm
    assert alarm_raised(alarm)

    quiet = RunAlarm(
        subject=LANE,
        signal=AlarmSignal.TALLY_UNMOVED,
        readings=firing(bound=2),
        bound=None,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )
    assert observe(quiet.readings) is None
    assert not alarm_raised(quiet)

    # The other arm of the same signal raises with no bound at all, so a
    # bound's absence is no answer to "is this raised?".
    scope = tally_unmoved(
        subject=SCOPE_SUBJECT,
        readings=scope_inputs(),
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )
    assert scope is not None
    assert scope.bound is None
    assert alarm_raised(scope)


def test_a_stored_record_whose_replay_disagrees_with_its_bound_refuses():
    alarm = observe(firing())
    assert alarm is not None
    claimed = alarm.model_copy(update={"bound": None})

    with pytest.raises(RunShapeReadError, match="replays to a bound"):
        alarm_raised(claimed)


@pytest.mark.parametrize(
    "damage",
    [
        "short",
        "long",
        "wrong-kind",
        "foreign-lane",
        "foreign-bound-field",
        "closed-was-never-owed",
        "closed-is-still-owed",
    ],
)
def test_malformed_foreign_or_inconsistent_readings_refuse(damage):
    values = list(firing())
    if damage == "short":
        values = values[:3]
    elif damage == "long":
        values = [*values, values[-1]]
    elif damage == "wrong-kind":
        values[0] = AlarmReading(
            source_ref=LANE.lane_key, value=TextEvidence(value="not a tally")
        )
    elif damage == "foreign-lane":
        values[1] = values[1].model_copy(update={"source_ref": "LANE-OTHER"})
    elif damage == "foreign-bound-field":
        values[3] = values[3].model_copy(update={"source_ref": "some_other_field"})
    elif damage == "closed-was-never-owed":
        values[2] = AlarmReading(
            source_ref=LANE.lane_key, value=ReferencesEvidence(value=("c-three",))
        )
    elif damage == "closed-is-still-owed":
        values[2] = AlarmReading(
            source_ref=LANE.lane_key, value=ReferencesEvidence(value=("c-one",))
        )

    with pytest.raises(RunShapeReadError) as caught:
        observe(tuple(values))

    assert caught.value.signal == AlarmSignal.TALLY_UNMOVED.value


#: The five identity tuples the arm reads, and one damaged reading for each: an
#: identity repeated in it. Every one of the five is checked by the code, so all
#: five are asked here — a repeat in the commits of either reading is counted
#: once by the set difference and leaves the arithmetic looking right, which is
#: exactly why the refusal cannot be pinned for one tuple and assumed for four.
REPEATED = {
    "anchor.open": (0, lambda: tally(("c-one", "c-one"))),
    "anchor.commits": (0, lambda: tally(commits=("sha-a", "sha-a"))),
    "latest.open": (1, lambda: tally(("c-one", "c-one"), ("sha-one", "sha-two"))),
    "latest.commits": (1, lambda: tally(("c-one", "c-two"), ("sha-one", "sha-one"))),
    "closed": (2, None),
}


@pytest.mark.parametrize("tuple_name", list(REPEATED))
def test_a_repeated_identity_in_any_of_the_five_tuples_refuses(tuple_name):
    """One reason for all five, so which tuple was damaged is not the answer.

    The reason is asserted and not only the signal: a repeat in the closed
    reading is also a closed identity still owed, and a check that had stopped
    looking at that tuple would refuse for the later reason and read as a pass.
    """
    position, damaged = REPEATED[tuple_name]
    values = list(firing())
    values[position] = AlarmReading(
        source_ref=LANE.lane_key,
        value=ReferencesEvidence(value=("c-one", "c-one"))
        if damaged is None
        else TallyEvidence(value=damaged()),
    )

    with pytest.raises(RunShapeReadError) as caught:
        observe(tuple(values))

    assert caught.value.reason == "an identity appears more than once"
    assert caught.value.signal == AlarmSignal.TALLY_UNMOVED.value


def test_a_subject_with_no_tally_arm_refuses():
    subject = IssueSubject(scope_key=SCOPE_KEY, issue_id="LANE-1")

    with pytest.raises(RunShapeReadError, match="no tally arm"):
        observe(firing(), subject=subject)
