"""The scope tally is replayable arithmetic over explicit roster readings."""

import json

import pytest

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import (
    CRITERIA_MARKER_SOURCE,
    TICKET_MARKER_SOURCE,
    tally_unmoved,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scope/opaque")
SUBJECT = AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key=SCOPE.key)


def reading(source, value):
    return AlarmReading(source_ref=source, value=json.dumps(value))


def inputs(*, roster=("one", "two"), labels=None):
    labels = {"one": ["criteria-ready"], "two": []} if labels is None else labels
    return (
        reading(TICKET_MARKER_SOURCE, "issue_labels.body-ready"),
        reading(CRITERIA_MARKER_SOURCE, "issue_labels.criteria-ready"),
        AlarmReading(source_ref=SCOPE.key, value=SCOPE.model_dump_json()),
        reading(SCOPE.key, roster),
        *(reading(key, value) for key, value in labels.items()),
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
        values[0] = reading("elsewhere", "issue_labels.body-ready")
    elif damage == "wrong-next-source":
        values[1] = reading("elsewhere", "issue_labels.criteria-ready")
    elif damage == "wrong-scope-source":
        values[2] = values[2].model_copy(update={"source_ref": "elsewhere"})
    elif damage == "wrong-roster-source":
        values[3] = reading("elsewhere", ["one", "two"])
    elif damage == "wrong-scope-key":
        values[2] = reading(SCOPE.key, {"kind": "project", "key": "foreign"})
    elif damage == "duplicate-roster":
        values[3] = reading(SCOPE.key, ["one", "one"])
    elif damage == "foreign-member":
        values.append(reading("foreign", ["criteria-ready"]))
    elif damage == "duplicate-member":
        values.append(values[4])
    elif damage == "malformed-member":
        values[4] = AlarmReading(source_ref="one", value="not JSON")
    elif damage == "string-members":
        values[3] = reading(SCOPE.key, "one")
    elif damage == "empty-member-key":
        values[3] = reading(SCOPE.key, [""])
    elif damage == "same-marker":
        values[1] = reading(CRITERIA_MARKER_SOURCE, "issue_labels.body-ready")
    elif damage == "scope-marker":
        values[0] = reading(TICKET_MARKER_SOURCE, "scope_labels.approved")
    elif damage == "malformed-marker":
        values[0] = reading(TICKET_MARKER_SOURCE, [])
    elif damage == "unqualified-marker":
        values[0] = reading(TICKET_MARKER_SOURCE, "body-ready")
    with pytest.raises(RunShapeReadError) as caught:
        observe(tuple(values))
    assert caught.value.signal == AlarmSignal.TALLY_UNMOVED.value


def test_lane_arm_is_explicitly_unavailable_not_a_second_signal():
    subject = AlarmSubject(
        kind=AlarmSubjectKind.LANE, scope_key=SCOPE.key, lane_key="lane/one"
    )
    with pytest.raises(RunShapeReadError, match="lane tally inputs"):
        observe(inputs(), subject=subject)


def test_graph_to_body_uses_the_same_signal_and_governed_source_pair():
    from kodezart.domain.run_shape import GROOM_MARKER_SOURCE

    values = list(inputs())
    values[0] = reading(GROOM_MARKER_SOURCE, "issue_labels.groomed")
    values[1] = reading(TICKET_MARKER_SOURCE, "issue_labels.body-ready")
    values[4] = reading("one", ["body-ready"])
    alarm = observe(tuple(values))
    assert alarm is not None
    assert alarm.signal is AlarmSignal.TALLY_UNMOVED
    assert observe(alarm.readings) == alarm


def test_skipping_the_middle_phase_is_not_an_adjacent_transition():
    from kodezart.domain.run_shape import GROOM_MARKER_SOURCE

    values = list(inputs())
    values[0] = reading(GROOM_MARKER_SOURCE, "issue_labels.groomed")
    with pytest.raises(RunShapeReadError, match="phase marker sources"):
        observe(tuple(values))
