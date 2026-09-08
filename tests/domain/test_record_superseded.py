"""A later recorded assertion supersedes only the same lane's same field."""

import json
from itertools import product
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import record_superseded
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    LaneFieldValue,
    RunAlarm,
)

ORDER = ("sha-z", "sha-a", "sha-y", "sha-b")


def subject(**changes):
    return AlarmSubject.model_validate(
        {
            "kind": AlarmSubjectKind.LANE,
            "scope_key": "scope/run",
            "lane_key": "lane/42",
            **changes,
        }
    )


def assertion(value, **changes):
    return {
        "lane_key": "lane/42",
        "field_key": "quality_gate",
        "value": value,
        **changes,
    }


def readings(
    *,
    record_sha="sha-z",
    event_sha="sha-a",
    record_value="red",
    event_value="green",
    order=ORDER,
):
    return (
        AlarmReading(
            source_ref="record/ref",
            value=json.dumps(assertion(record_value), indent=2),
            at_sha=record_sha,
        ),
        AlarmReading(
            source_ref="event/occurrence-7",
            value=json.dumps(assertion(event_value), indent=2),
            at_sha=event_sha,
        ),
        AlarmReading(
            source_ref="record/ref",
            value=json.dumps(order, indent=2),
            at_sha=order[-1] if order else None,
        ),
    )


def replace(original, slot, **changes):
    return tuple(
        reading.model_copy(update=changes) if index == slot else reading
        for index, reading in enumerate(original)
    )


def observe(original, target=None):
    return record_superseded(
        subject=target or subject(),
        readings=original,
        raised_at_sha="tick-sha",
        raised_by="supervisor/job",
    )


@pytest.mark.parametrize("record_sha,event_sha", list(product(ORDER, repeat=2)))
@pytest.mark.parametrize("same_value", [False, True])
def test_every_recorded_order_pair_uses_strict_position_and_value(
    record_sha, event_sha, same_value
):
    original = readings(
        record_sha=record_sha,
        event_sha=event_sha,
        event_value="red" if same_value else "green",
    )
    result = observe(original)
    expected = ORDER.index(event_sha) > ORDER.index(record_sha) and not same_value
    assert (result is not None) is expected
    if expected:
        assert result.signal is AlarmSignal.RECORD_SUPERSEDED
        assert result.subject == subject()
        assert result.bound is None
        assert result.readings == original
        assert result.raised_at_sha == "tick-sha"
        assert result.raised_by == "supervisor/job"


def test_reordering_the_same_recorded_commits_reverses_the_verdict():
    original = readings()
    assert observe(original) is not None
    reversed_history = replace(original, 2, value=json.dumps(tuple(reversed(ORDER))))
    assert observe(reversed_history) is None


@pytest.mark.parametrize(
    "value", ["", " ", "green", "0", "false", "unparsed narrative", "雪\n"]
)
def test_equal_opaque_values_stay_clean_after_json_decoding(value):
    original = readings(record_value=value, event_value=value)
    # JSON layout, key ordering, aliases and Unicode spelling are not field values.
    altered = replace(
        original,
        1,
        value=json.dumps(
            {"value": value, "fieldKey": "quality_gate", "laneKey": "lane/42"},
            ensure_ascii=False,
        ),
    )
    assert observe(altered) is None


@pytest.mark.parametrize("value", ["Red", "red ", "", "false", "not red"])
def test_values_are_not_trimmed_casefolded_or_interpreted(value):
    assert observe(readings(event_value=value)) is not None


@pytest.mark.parametrize("slot", [0, 1])
@pytest.mark.parametrize(
    "changes",
    [
        {"lane_key": "other"},
        {"lane_key": "Lane/42"},
        {"field_key": "forge_checks"},
        {"field_key": "Quality_Gate"},
    ],
)
def test_other_lane_or_field_cannot_supply_a_contrary_assertion(slot, changes):
    original = readings()
    body = assertion("red" if slot == 0 else "green", **changes)
    with pytest.raises(RunShapeReadError):
        observe(replace(original, slot, value=json.dumps(body)))


@pytest.mark.parametrize(
    "target",
    [
        subject(lane_key="other"),
        AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key="scope/run"),
        AlarmSubject(
            kind=AlarmSubjectKind.ISSUE,
            scope_key="scope/run",
            lane_key="lane/42",
            issue_id="issue/42",
        ),
    ],
)
def test_subject_must_identify_the_compared_lane(target):
    with pytest.raises(RunShapeReadError, match="different lanes"):
        observe(readings(), target)


def test_history_must_come_from_the_same_lane_record():
    with pytest.raises(RunShapeReadError, match="another record"):
        observe(replace(readings(), 2, source_ref="different/record"))


@pytest.mark.parametrize("slot", [0, 1])
@pytest.mark.parametrize("sha", [None, "not-in-history"])
@pytest.mark.parametrize("same_value", [False, True])
def test_assertions_require_a_recorded_sha_even_when_values_agree(
    slot, sha, same_value
):
    original = readings(event_value="red" if same_value else "green")
    with pytest.raises(
        RunShapeReadError, match="absent from recorded history"
    ) as raised:
        observe(replace(original, slot, at_sha=sha))
    assert raised.value.source_ref == original[slot].source_ref


@pytest.mark.parametrize(
    "order",
    [
        (),
        ("sha-z",),
        ("sha-a",),
        (*ORDER, "sha-z"),
        ("sha-z", "sha-a", " "),
        ("sha-z", "sha-a", 2),
        ("sha-z", "sha-a", True),
    ],
)
def test_incomplete_or_ambiguous_history_never_manufactures_a_clean_read(order):
    original = readings(event_value="red")
    with pytest.raises(RunShapeReadError):
        observe(replace(original, 2, value=json.dumps(order)))


@pytest.mark.parametrize("slot", range(3))
@pytest.mark.parametrize("value", ["not-json", "null", "{}", "7"])
def test_malformed_readings_raise_with_signal_source_and_validation_cause(slot, value):
    original = readings()
    with pytest.raises(RunShapeReadError) as raised:
        observe(replace(original, slot, value=value))
    assert raised.value.signal == "record_superseded"
    assert raised.value.source_ref == original[slot].source_ref
    assert isinstance(raised.value.__cause__, ValidationError)


@pytest.mark.parametrize(
    "body",
    [
        {"lane_key": "lane/42", "value": "green"},
        assertion("green", field_key=""),
        assertion("green", lane_key=" "),
        assertion(True),
        {**assertion("green"), "diagnosis": "invented"},
    ],
)
def test_assertion_projection_is_closed_and_carries_explicit_string_values(body):
    with pytest.raises(RunShapeReadError):
        observe(replace(readings(), 1, value=json.dumps(body)))


@pytest.mark.parametrize("extra", [False, True])
def test_incomplete_or_extra_readings_refuse(extra):
    original = readings()
    altered = (*original, original[0]) if extra else original[:-1]
    with pytest.raises(RunShapeReadError, match="incomplete readings"):
        observe(altered)


def test_field_projection_is_frozen_without_extending_the_alarm_payload():
    field = LaneFieldValue(lane_key="lane/42", field_key="quality_gate", value="red")
    assert field.model_dump(by_alias=True) == {
        "laneKey": "lane/42",
        "fieldKey": "quality_gate",
        "value": "red",
    }
    with pytest.raises(ValidationError):
        field.value = "green"
    alarm = observe(readings())
    assert set(alarm.model_dump()) == {
        "subject",
        "signal",
        "readings",
        "bound",
        "raised_at_sha",
        "raised_by",
    }


def test_alarm_replays_exact_raw_assertions_and_recorded_order():
    original = readings()
    alarm = observe(original)
    restored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    replayed = record_superseded(
        subject=restored.subject,
        readings=restored.readings,
        raised_at_sha=restored.raised_at_sha,
        raised_by=restored.raised_by,
    )
    assert replayed == alarm
    assert replayed.readings == original


def test_firing_clean_and_refusal_paths_make_zero_version_control_calls(monkeypatch):
    spies = []
    for name, method in vars(SubprocessGitService).items():
        if callable(method) and not name.startswith("_"):
            spy = AsyncMock(side_effect=AssertionError("repository access forbidden"))
            monkeypatch.setattr(SubprocessGitService, name, spy)
            spies.append(spy)
    assert observe(readings()) is not None
    assert observe(readings(event_value="red")) is None
    with pytest.raises(RunShapeReadError):
        observe(readings(event_sha="absent"))
    assert spies
    assert sum(spy.call_count for spy in spies) == 0
