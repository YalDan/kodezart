"""Contention is distinct run holders on one fully addressed surface."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.core.config import AppConfig
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import surface_contended
from kodezart.services.run_shape import observe_surface_contention
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
    surface_alarm_member_id,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

FIELD = "run_alarm_max_surface_holders"
SURFACE_JSON = TypeAdapter(WritableSurface)


def address(kind=SurfaceKind.MARKER_COMMENT, *, key="issue/42", marker="same:marker"):
    container = kind in {
        SurfaceKind.CONTAINER_DESCRIPTION,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    }
    return WritableSurface(
        kind=kind,
        ref=ScopeRef(kind=ScopeKind.PROJECT if container else ScopeKind.ISSUE, key=key),
        marker=marker if kind is SurfaceKind.MARKER_COMMENT else None,
    )


def subject(surface):
    return AlarmSubject(
        kind=AlarmSubjectKind.SURFACE,
        scope_key="scope-a",
        member_id=surface_alarm_member_id(surface),
    )


def readings(surface, *, holders=("job/run-a", "job/run-b"), limit=1):
    source = "provenance/" + surface_alarm_member_id(surface)
    return (
        AlarmReading(
            source_ref=source,
            value=SURFACE_JSON.dump_json(surface, indent=2).decode(),
            at_sha="observed-sha",
        ),
        AlarmReading(
            source_ref=source,
            value=json.dumps(holders, indent=2),
            at_sha="observed-sha",
        ),
        AlarmReading(source_ref=FIELD, value=json.dumps(limit)),
    )


def evaluate(surface, values):
    return surface_contended(
        subject=subject(surface),
        readings=values,
        raised_at_sha="raised-sha",
        raised_by="supervisor",
    )


@pytest.mark.parametrize("kind", list(SurfaceKind))
def test_every_surface_kind_uses_distinct_run_holders(kind):
    surface = address(kind)
    original = readings(surface)
    alarm = evaluate(surface, original)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.SURFACE_CONTENDED
    assert alarm.subject == subject(surface)
    assert alarm.bound == AlarmBound(
        config_field=FIELD, configured_value=1, observed_value=2
    )
    assert alarm.readings == original
    assert alarm.raised_at_sha == "raised-sha"
    assert alarm.raised_by == "supervisor"


@pytest.mark.parametrize(
    "holders", [(), ("job/run-a",), ("job/run-a", "job/run-a"), ("job/run-a",) * 20]
)
def test_repeated_writes_by_one_holder_do_not_inflate_contention(holders):
    surface = address()
    assert evaluate(surface, readings(surface, holders=holders)) is None


def test_distinct_runs_under_the_same_marker_use_the_same_signal():
    surface = address()
    original = readings(
        surface, holders=("run-1/holder-a", "run-1/holder-a", "run-2/holder-b")
    )
    alarm = evaluate(surface, original)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.SURFACE_CONTENDED
    assert alarm.bound.observed_value == 2
    assert alarm.readings[1] == original[1]
    assert alarm.subject.member_id == surface_alarm_member_id(surface)


@pytest.mark.parametrize(
    "other",
    [
        address(key="another/issue"),
        address(marker="another:marker"),
        address(SurfaceKind.ISSUE_DESCRIPTION),
    ],
)
def test_two_surfaces_with_one_holder_each_remain_independent(other):
    first = address()
    assert subject(first) != subject(other)
    for surface, holder in [
        (first, "holder-a"),
        (other, "holder-b"),
        (first, "holder-a"),
    ]:
        assert evaluate(surface, readings(surface, holders=(holder,))) is None


@pytest.mark.parametrize("limit", [2, 3])
def test_at_or_under_configured_holder_limit_is_clean(limit):
    surface = address()
    assert evaluate(surface, readings(surface, limit=limit)) is None


def test_zero_limit_observes_the_first_recorded_holder():
    surface = address()
    assert evaluate(surface, readings(surface, holders=(), limit=0)) is None
    alarm = evaluate(surface, readings(surface, holders=("job-a",), limit=0))
    assert alarm is not None
    assert alarm.bound.observed_value == 1
    assert alarm.bound.configured_value == 0


def test_holder_identity_is_opaque_and_case_sensitive():
    surface = address()
    alarm = evaluate(surface, readings(surface, holders=("job-A", "job-a")))
    assert alarm is not None
    assert alarm.bound.observed_value == 2


@pytest.mark.parametrize("kind", list(SurfaceKind))
def test_firing_fixture_replays_verbatim_address_and_history(kind):
    surface = address(kind)
    alarm = evaluate(surface, readings(surface, holders=("first", "second", "first")))
    assert alarm is not None
    restored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    assert (
        surface_contended(
            subject=restored.subject,
            readings=restored.readings,
            raised_at_sha=restored.raised_at_sha,
            raised_by=restored.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize("slot", range(3))
@pytest.mark.parametrize("value", ["not-json", "null", "{}"])
def test_unreadable_provenance_is_not_a_clean_observation(slot, value):
    surface = address()
    original = readings(surface)
    altered = tuple(
        reading.model_copy(update={"value": value}) if index == slot else reading
        for index, reading in enumerate(original)
    )
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(surface, altered)
    assert raised.value.signal == "surface_contended"
    assert raised.value.source_ref == original[slot].source_ref
    assert isinstance(raised.value.__cause__, ValidationError)


@pytest.mark.parametrize(
    "holders", [("",), ("  ",), (1,), ({"author": "account", "updatedAt": "later"},)]
)
def test_run_holders_cannot_be_inferred_from_invalid_or_vendor_shaped_rows(holders):
    surface = address()
    with pytest.raises(RunShapeReadError):
        evaluate(surface, readings(surface, holders=holders))


@pytest.mark.parametrize("limit", [-1, True, 1.5, "2"])
def test_limit_is_a_recorded_nonnegative_integer(limit):
    surface = address()
    with pytest.raises(RunShapeReadError):
        evaluate(surface, readings(surface, limit=limit))


@pytest.mark.parametrize("size", [0, 2, 4])
def test_complete_provenance_reading_set_is_required(size):
    surface = address()
    values = readings(surface)
    values = (*values, values[0]) if size == 4 else values[:size]
    with pytest.raises(RunShapeReadError, match="incomplete"):
        evaluate(surface, values)


@pytest.mark.parametrize(
    "other",
    [
        address(key="different/issue"),
        address(marker="different:marker"),
        address(SurfaceKind.ISSUE_DESCRIPTION),
    ],
)
def test_subject_cannot_borrow_another_surface_history(other):
    with pytest.raises(RunShapeReadError, match="another surface"):
        evaluate(address(), readings(other))


@pytest.mark.parametrize("slot", [0, 1, 2])
def test_source_identity_and_bound_name_cannot_be_substituted(slot):
    surface = address()
    values = tuple(
        reading.model_copy(update={"source_ref": "other/source"})
        if index == slot
        else reading
        for index, reading in enumerate(readings(surface))
    )
    with pytest.raises(RunShapeReadError):
        evaluate(surface, values)


def test_surface_observation_requires_a_surface_subject():
    with pytest.raises(RunShapeReadError, match="another surface"):
        surface_contended(
            subject=AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key="scope-a"),
            readings=readings(address()),
            raised_at_sha="sha",
            raised_by="holder",
        )


def test_configuration_assembly_uses_the_actual_limit_and_preserves_inputs(monkeypatch):
    monkeypatch.setenv("KODEZART_RUN_ALARM_MAX_SURFACE_HOLDERS", "2")
    config = AppConfig(_env_file=None)
    assert config.run_alarm_max_surface_holders == 2
    surface = address()
    original = readings(surface, holders=("one", "two", "three"))
    alarm = observe_surface_contention(
        config=config,
        subject=subject(surface),
        surface=original[0],
        holder_history=original[1],
        raised_at_sha="sha",
        raised_by="holder",
    )
    assert alarm is not None
    assert alarm.bound == AlarmBound(
        config_field=FIELD, configured_value=2, observed_value=3
    )
    assert alarm.readings[:2] == original[:2]


def test_negative_holder_limit_refuses_configuration():
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, run_alarm_max_surface_holders=-1)
