"""Missing write-backs and inconsistent row counts use recorded facts only."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_alarm_record import surface_alarm_member_id
from kodezart.domain.run_shape import commits_ahead_of_record, write_back_missing
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CommitsEvidence,
    CountEvidence,
    LaneSubject,
    PresenceEvidence,
    RunAlarm,
    SurfaceEvidence,
    SurfaceSubject,
    TextEvidence,
)
from kodezart.types.domain.run_state import LaneCommit
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface


def surface(kind=SurfaceKind.MARKER_COMMENT, *, key="issue/42", marker="record:key"):
    container = kind in {
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    }
    return WritableSurface(
        kind=kind,
        ref=ScopeRef(kind=ScopeKind.PROJECT if container else ScopeKind.ISSUE, key=key),
        marker=marker if kind is SurfaceKind.MARKER_COMMENT else None,
    )


def surface_subject(address):
    return SurfaceSubject(
        scope_key="scope/run",
        surface=address,
    )


def write_readings(address, *, present=False):
    return (
        AlarmReading(
            source_ref="event/occurrence-7",
            value=SurfaceEvidence(value=address),
            at_sha="event-sha",
        ),
        AlarmReading(
            source_ref=surface_alarm_member_id(address),
            value=PresenceEvidence(value=present),
            at_sha="record-observed-sha",
        ),
    )


def lane_subject():
    return LaneSubject(scope_key="scope/run", lane_key="lane/42")


def commit_readings(*, count=3, rows=None, head="declared-head"):
    if rows is None:
        rows = [
            {"sha": "old", "subject": "Recorded work", "issue_id": "issue/42"},
            {"sha": head, "subject": "", "issue_id": "another/7"},
        ]
    return tuple(
        AlarmReading(
            source_ref="lane-record/ref",
            value=value,
            at_sha=head,
        )
        for value in (
            TextEvidence(value="lane/42"),
            TextEvidence(value=head),
            CountEvidence(value=count),
            CommitsEvidence(
                value=tuple(LaneCommit.model_validate(row) for row in rows)
            ),
        )
    )


def evaluate(function, subject, readings):
    return function(
        subject=subject,
        readings=readings,
        raised_at_sha="alarm-sha",
        raised_by="supervisor/job",
    )


def replace(readings, slot, **changes):
    return tuple(
        reading.model_copy(update=changes) if index == slot else reading
        for index, reading in enumerate(readings)
    )


@pytest.mark.parametrize("kind", list(SurfaceKind))
def test_missing_write_names_exact_owed_surface_and_present_record_is_clean(kind):
    address = surface(kind)
    subject = surface_subject(address)
    readings = write_readings(address)
    alarm = evaluate(write_back_missing, subject, readings)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.WRITE_BACK_MISSING
    assert alarm.subject == subject
    assert alarm.bound is None
    assert alarm.readings == readings
    assert alarm.raised_at_sha == "alarm-sha"
    assert alarm.raised_by == "supervisor/job"
    assert (
        evaluate(write_back_missing, subject, write_readings(address, present=True))
        is None
    )


@pytest.mark.parametrize(
    "other",
    [
        surface(key="other/42"),
        surface(marker="record:other"),
        surface(SurfaceKind.ISSUE_DESCRIPTION),
    ],
)
def test_presence_on_another_surface_cannot_discharge_the_event(other):
    address = surface()
    readings = write_readings(address, present=True)
    with pytest.raises(RunShapeReadError, match="another surface"):
        evaluate(
            write_back_missing,
            surface_subject(address),
            replace(readings, 1, source_ref=surface_alarm_member_id(other)),
        )


@pytest.mark.parametrize("present", [None, 0, 1, "false", "true", {}, []])
def test_only_an_explicit_successful_lookup_boolean_can_answer_presence(present):
    address = surface()
    with pytest.raises(ValidationError) as raised:
        evaluate(
            write_back_missing,
            surface_subject(address),
            write_readings(address, present=present),
        )
    assert raised.value.errors()[0]["type"] == "bool_type"
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "subject", [lane_subject(), surface_subject(surface(key="other"))]
)
def test_write_back_subject_must_identify_owed_surface(subject):
    with pytest.raises(RunShapeReadError, match="another surface"):
        evaluate(write_back_missing, subject, write_readings(surface()))


@pytest.mark.parametrize("count", [0, 1, 3, 20])
def test_count_disagreement_in_either_direction_fires_without_a_bound(count):
    readings = commit_readings(count=count)
    alarm = evaluate(commits_ahead_of_record, lane_subject(), readings)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.COMMITS_AHEAD_OF_RECORD
    assert alarm.subject == lane_subject()
    assert alarm.bound is None
    assert alarm.readings == readings
    assert alarm.raised_at_sha == "alarm-sha"
    assert alarm.raised_by == "supervisor/job"


@pytest.mark.parametrize("count,rows", [(0, []), (2, None)])
def test_agreeing_count_and_rows_raise_nothing(count, rows):
    assert (
        evaluate(
            commits_ahead_of_record,
            lane_subject(),
            commit_readings(count=count, rows=rows),
        )
        is None
    )


def test_a_wholly_stale_record_with_agreeing_terms_is_intentionally_invisible():
    # The declared head is evidence from the record, never a repository lookup.
    readings = commit_readings(count=2, head="wholly-stale-head")
    assert evaluate(commits_ahead_of_record, lane_subject(), readings) is None


def test_no_commit_subject_or_issue_heuristic_changes_the_row_count():
    rows = [
        {"sha": "opaque-a", "subject": "", "issue_id": "another/7"},
        {
            "sha": "opaque-b",
            "subject": "mentions issue/42 twice issue/42",
            "issue_id": "external/99",
        },
    ]
    assert (
        evaluate(
            commits_ahead_of_record, lane_subject(), commit_readings(count=2, rows=rows)
        )
        is None
    )


@pytest.mark.parametrize("slot", range(4))
def test_mixing_record_sources_refuses_instead_of_manufacturing_agreement(slot):
    with pytest.raises(RunShapeReadError, match="different lane records"):
        evaluate(
            commits_ahead_of_record,
            lane_subject(),
            replace(commit_readings(), slot, source_ref="other-record"),
        )


@pytest.mark.parametrize("slot", range(4))
def test_mixing_recorded_heads_refuses(slot):
    with pytest.raises(RunShapeReadError, match="declared head"):
        evaluate(
            commits_ahead_of_record,
            lane_subject(),
            replace(commit_readings(), slot, at_sha="another-head"),
        )


def test_unstamped_readings_keep_the_declared_head_without_inventing_a_stamp():
    readings = tuple(
        reading.model_copy(update={"at_sha": None}) for reading in commit_readings()
    )
    alarm = evaluate(commits_ahead_of_record, lane_subject(), readings)
    assert alarm is not None
    assert all(reading.at_sha is None for reading in alarm.readings)


@pytest.mark.parametrize(
    "subject",
    [
        surface_subject(surface()),
        lane_subject().model_copy(update={"lane_key": "other"}),
    ],
)
def test_count_subject_must_identify_the_recorded_lane(subject):
    with pytest.raises(RunShapeReadError, match="another lane"):
        evaluate(commits_ahead_of_record, subject, commit_readings())


@pytest.mark.parametrize("count", [-1, True, "2", 2.5, None])
def test_declared_count_must_be_a_nonnegative_integer(count):
    with pytest.raises(ValidationError):
        evaluate(commits_ahead_of_record, lane_subject(), commit_readings(count=count))


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([{"sha": "x", "subject": "s"}], ValidationError),
        (
            [{"sha": "x", "subject": "s", "issue_id": "i", "explanation": "extra"}],
            ValidationError,
        ),
        ([{"sha": 3, "subject": "s", "issue_id": "i"}], ValidationError),
        ([{"sha": " ", "subject": "s", "issue_id": "i"}], RunShapeReadError),
        ([{"sha": "x", "subject": "s", "issue_id": "i"}] * 2, RunShapeReadError),
        (["x"], ValidationError),
        ({}, ValidationError),
        (None, ValidationError),
    ],
)
def test_unreadable_or_ambiguous_commit_rows_do_not_clear_the_signal(rows, expected):
    with pytest.raises(expected):
        evidence = CommitsEvidence.model_validate({"value": rows})
        readings = replace(commit_readings(count=0), 3, value=evidence)
        evaluate(commits_ahead_of_record, lane_subject(), readings)


@pytest.mark.parametrize(
    "function,subject,readings",
    [
        (write_back_missing, surface_subject(surface()), write_readings(surface())),
        (commits_ahead_of_record, lane_subject(), commit_readings()),
    ],
)
def test_alarms_replay_from_their_exact_ordered_payload(function, subject, readings):
    alarm = evaluate(function, subject, readings)
    restored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    assert (
        function(
            subject=restored.subject,
            readings=restored.readings,
            raised_at_sha=restored.raised_at_sha,
            raised_by=restored.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize(
    "function,subject,readings",
    [
        (write_back_missing, surface_subject(surface()), write_readings(surface())),
        (commits_ahead_of_record, lane_subject(), commit_readings()),
    ],
)
@pytest.mark.parametrize("extra", [False, True])
def test_missing_or_extra_input_is_a_typed_refusal(function, subject, readings, extra):
    altered = (*readings, readings[0]) if extra else readings[:-1]
    with pytest.raises(RunShapeReadError, match="incomplete readings"):
        evaluate(function, subject, altered)


def test_commit_model_is_the_frozen_closed_three_field_record():
    row = LaneCommit(sha="opaque", subject="", issue_id="own/key")
    assert row.model_dump(by_alias=True) == {
        "sha": "opaque",
        "subject": "",
        "issueId": "own/key",
    }
    with pytest.raises(ValidationError):
        row.sha = "changed"
    with pytest.raises(ValidationError):
        LaneCommit(sha="opaque", subject="s", issue_id="own/key", extra="invalid")


@pytest.mark.parametrize("slot", [0, 1])
@pytest.mark.parametrize(
    "value", [CountEvidence(value=7), TextEvidence(value=""), TextEvidence(value=" ")]
)
def test_lane_and_declared_head_require_readable_opaque_identities(slot, value):
    with pytest.raises(RunShapeReadError):
        evaluate(
            commits_ahead_of_record,
            lane_subject(),
            replace(commit_readings(), slot, value=value),
        )


def test_both_firing_and_clean_paths_make_zero_version_control_calls(monkeypatch):
    calls = []
    for name, method in vars(SubprocessGitService).items():
        if callable(method) and not name.startswith("_"):
            spy = AsyncMock(side_effect=AssertionError("repository access forbidden"))
            monkeypatch.setattr(SubprocessGitService, name, spy)
            calls.append(spy)
    address = surface()
    for present in (False, True):
        evaluate(
            write_back_missing,
            surface_subject(address),
            write_readings(address, present=present),
        )
    for count in (2, 3):
        evaluate(commits_ahead_of_record, lane_subject(), commit_readings(count=count))
    assert calls
    assert sum(spy.call_count for spy in calls) == 0
