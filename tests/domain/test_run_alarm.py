"""Run-shape observations retain one valid subject and immutable readings."""

import json

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TALLY_UNMOVED", "tally_unmoved"),
        ("TALLY_REGRESSED", "tally_regressed"),
        ("LAPSE_UNDISCHARGED", "lapse_undischarged"),
        ("ESCALATION_AGEING", "escalation_ageing"),
        ("WRITE_BACK_MISSING", "write_back_missing"),
        ("SURFACE_CONTENDED", "surface_contended"),
        ("RECORD_SUPERSEDED", "record_superseded"),
        ("COMPOSITION_SUBSTITUTED", "composition_substituted"),
        ("BARREN_TICK_WITH_DIFF_GROWTH", "barren_tick_with_diff_growth"),
        ("COMMITS_AHEAD_OF_RECORD", "commits_ahead_of_record"),
        ("RULINGS_OUTPACE_CLOSURES", "rulings_outpace_closures"),
        (
            "STRUCTURAL_WRITE_UNCROSSES_MILESTONE",
            "structural_write_uncrosses_milestone",
        ),
    ],
)
def test_declared_signal_values(name, value):
    assert AlarmSignal[name].value == value


@pytest.mark.parametrize("kind", AlarmSubjectKind)
def test_subject_kind_wire_values(kind):
    assert (
        kind.value
        == {
            AlarmSubjectKind.SCOPE: "scope",
            AlarmSubjectKind.LANE: "lane",
            AlarmSubjectKind.ISSUE: "issue",
            AlarmSubjectKind.CRITERION: "criterion",
            AlarmSubjectKind.SURFACE: "surface",
            AlarmSubjectKind.ESCALATION: "escalation",
        }[kind]
    )


def subject_data(kind):
    data = {"kind": kind, "scope_key": "scope/opaque"}
    if kind is AlarmSubjectKind.LANE:
        data["lane_key"] = "lane/opaque"
    if kind in {AlarmSubjectKind.ISSUE, AlarmSubjectKind.CRITERION}:
        data["issue_id"] = "parent/opaque"
    if kind is AlarmSubjectKind.CRITERION:
        data["member_id"] = "criterion/opaque"
    if kind is AlarmSubjectKind.ESCALATION:
        data["member_id"] = "question/opaque"
    if kind is AlarmSubjectKind.SURFACE:
        data["member_id"] = surface_alarm_member_id(
            WritableSurface(
                kind=SurfaceKind.ISSUE_DESCRIPTION,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue/opaque"),
            )
        )
    return data


@pytest.mark.parametrize("kind", AlarmSubjectKind)
def test_valid_required_region_round_trips(kind):
    subject = AlarmSubject.model_validate(subject_data(kind))
    assert AlarmSubject.model_validate_json(subject.model_dump_json()) == subject


@pytest.mark.parametrize(
    ("kind", "field"),
    [(kind, "scope_key") for kind in AlarmSubjectKind]
    + [
        (AlarmSubjectKind.LANE, "lane_key"),
        (AlarmSubjectKind.ISSUE, "issue_id"),
        (AlarmSubjectKind.CRITERION, "issue_id"),
        (AlarmSubjectKind.CRITERION, "member_id"),
        (AlarmSubjectKind.SURFACE, "member_id"),
        (AlarmSubjectKind.ESCALATION, "member_id"),
    ],
)
@pytest.mark.parametrize("absent", [None, "", " \n "])
def test_every_subject_rejects_a_missing_required_identity(kind, field, absent):
    data = subject_data(kind)
    data[field] = absent
    with pytest.raises(ValidationError):
        AlarmSubject.model_validate(data)


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        (AlarmSubjectKind.SCOPE, "lane_key"),
        (AlarmSubjectKind.SCOPE, "issue_id"),
        (AlarmSubjectKind.SCOPE, "member_id"),
        (AlarmSubjectKind.LANE, "issue_id"),
        (AlarmSubjectKind.LANE, "member_id"),
        (AlarmSubjectKind.ISSUE, "member_id"),
    ],
)
def test_broader_subject_cannot_smuggle_a_narrower_identity(kind, field):
    data = subject_data(kind)
    data[field] = "some/other/member"
    with pytest.raises(ValidationError):
        AlarmSubject.model_validate(data)


@pytest.mark.parametrize("criterion_key", ["EXT/42", "AC-7", "9f2d:child@other"])
def test_criterion_uses_its_opaque_own_key(criterion_key):
    subject = AlarmSubject(
        kind=AlarmSubjectKind.CRITERION,
        scope_key="scope",
        issue_id="parent",
        member_id=criterion_key,
    )
    assert subject.member_id == criterion_key


@pytest.mark.parametrize("kind", SurfaceKind)
def test_surface_subject_preserves_every_address_kind(kind):
    scope_kind = (
        ScopeKind.PROJECT
        if kind
        in {SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE}
        else ScopeKind.ISSUE
    )
    surface = WritableSurface(
        kind=kind,
        ref=ScopeRef(kind=scope_kind, key="opaque/key:with:separator"),
        marker='marker:one"/two' if kind is SurfaceKind.MARKER_COMMENT else None,
    )
    member = surface_alarm_member_id(surface)
    subject = AlarmSubject(
        kind=AlarmSubjectKind.SURFACE, scope_key="scope", member_id=member
    )
    assert subject.member_id == member
    assert AlarmSubject.model_validate_json(subject.model_dump_json()) == subject
    assert json.loads(member)["ref"]["key"] == surface.ref.key
    assert json.loads(member)["marker"] == surface.marker


@pytest.mark.parametrize(
    "member",
    [
        "issue/opaque",
        "[]",
        "{}",
        '{"kind":"unknown","ref":{"kind":"issue","key":"x"},"marker":null}',
        '{"kind":"issue_description","ref":{"kind":"project","key":"x"},"marker":null}',
        '{"kind":"marker_comment","ref":{"kind":"issue","key":"x"},"marker":null}',
        '{"kind":"issue_description","ref":{"kind":"issue","key":"x"},"marker":"unexpected"}',
    ],
)
def test_surface_subject_rejects_an_unreadable_or_invalid_address(member):
    with pytest.raises(ValidationError):
        AlarmSubject(kind=AlarmSubjectKind.SURFACE, scope_key="scope", member_id=member)


def test_surface_identity_has_one_canonical_spelling():
    data = subject_data(AlarmSubjectKind.SURFACE)
    data["member_id"] = json.dumps(json.loads(data["member_id"]), indent=2)
    with pytest.raises(ValidationError, match="canonical"):
        AlarmSubject.model_validate(data)


def alarm_data():
    return {
        "subject": subject_data(AlarmSubjectKind.LANE),
        "signal": AlarmSignal.ESCALATION_AGEING,
        "readings": [
            {
                "source_ref": "escalation/one",
                "value": "UNRESOLVED\n",
                "at_sha": "0000000",
            },
            {"source_ref": "count/one", "value": " 004 ", "at_sha": None},
        ],
        "bound": {
            "config_field": "run_alarm_escalation_age_max_commits",
            "configured_value": 3,
            "observed_value": 4,
        },
        "raised_at_sha": "0000000",
        "raised_by": "job/opaque",
    }


@pytest.mark.parametrize(
    "subjects",
    [
        [],
        [subject_data(AlarmSubjectKind.SCOPE)],
        [subject_data(AlarmSubjectKind.SCOPE)] * 2,
    ],
)
def test_alarm_rejects_a_subject_sequence(subjects):
    data = alarm_data()
    data["subject"] = subjects
    with pytest.raises(ValidationError):
        RunAlarm.model_validate(data)


def test_alarm_rejects_empty_readings():
    data = alarm_data()
    data["readings"] = []
    with pytest.raises(ValidationError):
        RunAlarm.model_validate(data)


def test_alarm_preserves_reading_order_and_verbatim_values():
    alarm = RunAlarm.model_validate(alarm_data())
    assert tuple(reading.value for reading in alarm.readings) == (
        "UNRESOLVED\n",
        " 004 ",
    )
    assert tuple(reading.source_ref for reading in alarm.readings) == (
        "escalation/one",
        "count/one",
    )
    assert RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True)) == alarm
    assert alarm.raised_at_sha == "0000000"


def test_readings_are_frozen_independently_of_the_input_list():
    data = alarm_data()
    alarm = RunAlarm.model_validate(data)
    data["readings"].clear()
    assert tuple(reading.value for reading in alarm.readings) == (
        "UNRESOLVED\n",
        " 004 ",
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        alarm.readings[0].value = "changed"
    with pytest.raises(ValidationError, match="frozen_instance"):
        alarm.subject.scope_key = "changed"
    with pytest.raises(ValidationError, match="frozen_instance"):
        alarm.bound.observed_value = 0
    with pytest.raises(ValidationError, match="frozen_instance"):
        alarm.raised_by = "changed"


def test_reading_collection_cannot_be_changed_through_the_frozen_alarm():
    alarm = RunAlarm.model_validate(alarm_data())
    before = alarm.model_dump_json()
    replacement = AlarmReading(source_ref="other/reading", value="changed")
    with pytest.raises(TypeError):
        alarm.readings[0] = replacement
    assert alarm.model_dump_json() == before
    with pytest.raises(ValidationError, match="frozen_instance"):
        alarm.readings += (replacement,)
    assert alarm.model_dump_json() == before


def test_empty_read_value_is_a_valid_verbatim_reading():
    reading = AlarmReading(source_ref="record/empty", value="", at_sha=None)
    assert reading.value == ""


@pytest.mark.parametrize("field", ["configured_value", "observed_value"])
def test_bound_rejects_a_negative_count(field):
    data = {
        "config_field": "run_alarm_max_surface_holders",
        "configured_value": 1,
        "observed_value": 2,
    }
    data[field] = -1
    with pytest.raises(ValidationError):
        AlarmBound.model_validate(data)
