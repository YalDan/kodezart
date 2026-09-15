"""The scope tally is replayable arithmetic over explicit roster readings."""

import pytest
from pydantic import ValidationError

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import (
    CRITERIA_MARKER_SOURCE,
    TICKET_MARKER_SOURCE,
    tally_unmoved,
)
from kodezart.types.domain.mandate_graph import LaneGraphSnapshot
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    GraphEvidence,
    LabelsEvidence,
    LaneSubject,
    ReferencesEvidence,
    RunEventProjection,
    RunEventsEvidence,
    ScopeEvidence,
    ScopeSubject,
    TextEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue, WorkflowStateKind
from tests.tracker.conftest import FIXTURE_NOW

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scope/opaque")
SUBJECT = ScopeSubject(scope_key=SCOPE.key)
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone/one")
LANE = "lane/one"
FIRE = "KOD-900"
CRITERION = "KOD-901"


def reading(source, value):
    return AlarmReading(source_ref=source, value=value)


def inputs(*, roster=("one", "two"), labels=None):
    labels = {"one": ["criteria-ready"], "two": []} if labels is None else labels
    return (
        reading(TICKET_MARKER_SOURCE, TextEvidence(value="issue_labels.body-ready")),
        reading(
            CRITERIA_MARKER_SOURCE, TextEvidence(value="issue_labels.criteria-ready")
        ),
        AlarmReading(source_ref=SCOPE.key, value=ScopeEvidence(value=SCOPE)),
        reading(SCOPE.key, ReferencesEvidence(value=roster)),
        *(
            reading(key, LabelsEvidence(value=None if value is None else tuple(value)))
            for key, value in labels.items()
        ),
    )


def observe(readings, subject=SUBJECT):
    return tally_unmoved(
        subject=subject,
        readings=readings,
        raised_at_sha="tick-sha",
        raised_by="supervisor/holder",
    )


@pytest.mark.parametrize(
    "labels",
    [
        {"one": ["criteria-ready"], "two": []},
        {"one": ["criteria-ready", "body-ready"], "two": []},
        {"one": ["criteria-ready"], "two": None},
        {"one": ["criteria-ready"]},
    ],
)
async def test_zero_partial_empty_and_missing_marker_reads_remain_open(labels):
    readings = inputs(labels=labels)
    alarm = observe(readings)
    assert alarm is not None
    assert alarm.subject == SUBJECT
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert alarm.bound is None
    assert alarm.readings == readings
    assert alarm.raised_at_sha == "tick-sha"
    assert alarm.raised_by == "supervisor/holder"
    assert observe(alarm.readings) == alarm


@pytest.mark.parametrize(
    "labels",
    [
        {"one": ["criteria-ready", "body-ready"], "two": ["body-ready"]},
        {"one": ["body-ready"], "two": ["body-ready"]},
        {"one": [], "two": []},
        {"one": None},
        {},
    ],
)
def test_complete_marker_roster_or_no_entry_is_quiet(labels):
    assert observe(inputs(labels=labels)) is None


def test_empty_roster_is_quiet():
    assert observe(inputs(roster=(), labels={})) is None


def test_duplicate_label_values_never_multiply_member_count():
    assert (
        observe(inputs(labels={"one": ["criteria-ready", "body-ready", "body-ready"]}))
        is not None
    )


@pytest.mark.parametrize(
    "damage",
    [
        "short",
        "wrong-current-source",
        "wrong-next-source",
        "wrong-scope-source",
        "wrong-roster-source",
        "wrong-scope-key",
        "duplicate-roster",
        "foreign-member",
        "duplicate-member",
        "malformed-member",
        "string-members",
        "empty-member-key",
        "same-marker",
        "scope-marker",
        "malformed-marker",
        "unqualified-marker",
    ],
)
def test_incomplete_malformed_or_foreign_data_refuses(damage):
    values = list(inputs())
    if damage == "short":
        values = values[:3]
    elif damage == "wrong-current-source":
        values[0] = reading("elsewhere", TextEvidence(value="issue_labels.body-ready"))
    elif damage == "wrong-next-source":
        values[1] = reading(
            "elsewhere", TextEvidence(value="issue_labels.criteria-ready")
        )
    elif damage == "wrong-scope-source":
        values[2] = values[2].model_copy(update={"source_ref": "elsewhere"})
    elif damage == "wrong-roster-source":
        values[3] = reading("elsewhere", ReferencesEvidence(value=("one", "two")))
    elif damage == "wrong-scope-key":
        values[2] = reading(
            SCOPE.key,
            ScopeEvidence(value=ScopeRef(kind=ScopeKind.PROJECT, key="foreign")),
        )
    elif damage == "duplicate-roster":
        values[3] = reading(SCOPE.key, ReferencesEvidence(value=("one", "two", "one")))
    elif damage == "foreign-member":
        values.append(reading("foreign", LabelsEvidence(value=("criteria-ready",))))
    elif damage == "duplicate-member":
        values.append(values[4])
    elif damage == "malformed-member":
        values[4] = AlarmReading(
            source_ref="one", value=TextEvidence(value="wrong evidence arm")
        )
    elif damage == "string-members":
        values[3] = reading(SCOPE.key, TextEvidence(value="one"))
    elif damage == "empty-member-key":
        with pytest.raises(ValidationError):
            reading(SCOPE.key, ReferencesEvidence(value=("",)))
        return
    elif damage == "same-marker":
        values[1] = reading(
            CRITERIA_MARKER_SOURCE, TextEvidence(value="issue_labels.body-ready")
        )
    elif damage == "scope-marker":
        values[0] = reading(
            TICKET_MARKER_SOURCE, TextEvidence(value="scope_labels.approved")
        )
    elif damage == "malformed-marker":
        values[0] = reading(TICKET_MARKER_SOURCE, LabelsEvidence(value=()))
    elif damage == "unqualified-marker":
        values[0] = reading(TICKET_MARKER_SOURCE, TextEvidence(value="body-ready"))
    with pytest.raises(RunShapeReadError) as caught:
        observe(tuple(values))
    assert caught.value.signal == AlarmSignal.TALLY_UNMOVED.value


def lane_issue(key, state, *, parent=None, labels=frozenset()):
    return TrackerIssue(
        issue_key=key,
        title=key,
        body="body",
        priority=IssuePriority.NONE,
        state_name=state.value,
        state_kind=state,
        queue_states=frozenset(),
        issue_labels=labels,
        team_key=None,
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/{key}",
        parent_key=parent,
        milestone_key=MILESTONE.key,
    )


def lane_inputs():
    """The lane arm's own shape: the lane's posted events, then its subtree."""
    fire = lane_issue(FIRE, WorkflowStateKind.STARTED)
    criterion = lane_issue(
        CRITERION,
        WorkflowStateKind.UNSTARTED,
        parent=FIRE,
        labels=frozenset({"criterion"}),
    )
    subtree = (fire, criterion)
    return (
        reading(
            LANE,
            RunEventsEvidence(
                value=(
                    RunEventProjection(
                        kind=RunEventKind.EVALUATOR_ACCEPTED, subject_key=None
                    ),
                )
            ),
        ),
        reading(
            LANE,
            GraphEvidence(
                value=LaneGraphSnapshot(
                    lane_key=LANE,
                    fire_key=FIRE,
                    milestone=MILESTONE,
                    subtree=subtree,
                    milestone_members=subtree,
                    supersessions=(),
                )
            ),
        ),
    )


def test_the_lane_arm_computes_from_its_own_readings():
    subject = LaneSubject(scope_key=SCOPE.key, lane_key=LANE)
    readings = lane_inputs()
    alarm = observe(readings, subject=subject)
    assert alarm is not None
    assert alarm.subject == subject
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert alarm.readings == readings
    assert observe(alarm.readings, subject=subject) == alarm


def test_scope_readings_handed_to_the_lane_arm_are_named_as_its_inputs_missing():
    subject = LaneSubject(scope_key=SCOPE.key, lane_key=LANE)
    with pytest.raises(RunShapeReadError) as caught:
        observe(inputs(), subject=subject)
    assert caught.value.reason == "lane tally inputs are unreadable"
    assert caught.value.signal == AlarmSignal.TALLY_UNMOVED.value


def test_graph_to_body_uses_the_same_signal_and_governed_source_pair():
    from kodezart.domain.run_shape import GROOM_MARKER_SOURCE

    values = list(inputs())
    values[0] = reading(GROOM_MARKER_SOURCE, TextEvidence(value="issue_labels.groomed"))
    values[1] = reading(
        TICKET_MARKER_SOURCE, TextEvidence(value="issue_labels.body-ready")
    )
    values[4] = reading("one", LabelsEvidence(value=("body-ready",)))
    alarm = observe(tuple(values))
    assert alarm is not None
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert observe(alarm.readings) == alarm


def test_skipping_the_middle_phase_is_not_an_adjacent_transition():
    from kodezart.domain.run_shape import GROOM_MARKER_SOURCE

    values = list(inputs())
    values[0] = reading(GROOM_MARKER_SOURCE, TextEvidence(value="issue_labels.groomed"))
    with pytest.raises(RunShapeReadError, match="phase marker sources"):
        observe(tuple(values))


def test_consistent_recorded_scope_cannot_be_replayed_under_a_foreign_subject():
    foreign = ScopeSubject(scope_key="different/scope")
    with pytest.raises(RunShapeReadError, match="scope identity disagrees"):
        observe(inputs(), subject=foreign)
