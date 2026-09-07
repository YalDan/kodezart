"""Recorded branch growth is measured against identity-based progress."""

import json

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import barren_tick_with_diff_growth
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)

FILES_FIELD = "run_alarm_barren_tick_max_files_changed"
COMMITS_FIELD = "run_alarm_barren_tick_max_commits_ahead"
SUBJECT = AlarmSubject(
    kind=AlarmSubjectKind.LANE, scope_key="scope-a", lane_key="lane-a"
)


def readings(
    *,
    previous=("EXT/42", "EXT/43"),
    closed=(),
    files=8,
    commits=3,
    max_files=7,
    max_commits=5,
):
    return tuple(
        AlarmReading(source_ref=source, value=json.dumps(value, indent=2), at_sha=sha)
        for source, value, sha in (
            ("previous-tick#open", previous, "previous-sha"),
            ("current-criteria#closed", closed, None),
            ("lane-record#files-changed", files, "current-sha"),
            ("lane-record#commits-ahead", commits, "current-sha"),
            (FILES_FIELD, max_files, None),
            (COMMITS_FIELD, max_commits, None),
        )
    )


def evaluate(values):
    return barren_tick_with_diff_growth(
        subject=SUBJECT,
        readings=values,
        raised_at_sha="observed-sha",
        raised_by="holder",
    )


@pytest.mark.parametrize(
    "files_limit,commits_limit,field,configured,observed",
    [
        (7, 5, FILES_FIELD, 7, 8),
        (8, 2, COMMITS_FIELD, 2, 3),
        (7, 2, FILES_FIELD, 7, 8),
    ],
)
def test_each_growth_term_names_its_own_configured_bound(
    files_limit, commits_limit, field, configured, observed
):
    original = readings(max_files=files_limit, max_commits=commits_limit)
    alarm = evaluate(original)
    assert alarm is not None
    assert alarm.subject == SUBJECT
    assert alarm.signal is AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH
    assert alarm.bound == AlarmBound(
        config_field=field, configured_value=configured, observed_value=observed
    )
    assert alarm.readings == original
    assert alarm.raised_at_sha == "observed-sha"
    assert alarm.raised_by == "holder"


@pytest.mark.parametrize("files_limit,commits_limit", [(8, 3), (9, 3), (8, 4), (9, 4)])
def test_at_or_under_both_limits_is_clean(files_limit, commits_limit):
    assert evaluate(readings(max_files=files_limit, max_commits=commits_limit)) is None


def test_exactly_one_previously_open_identity_closed_is_progress():
    assert evaluate(readings(closed=("EXT/43",), max_files=0, max_commits=0)) is None


@pytest.mark.parametrize(
    "previous,closed", [((), ()), (("EXT/42",), ()), (("EXT/42",), ("new/closed",))]
)
def test_absent_or_newly_closed_references_do_not_fake_previous_progress(
    previous, closed
):
    assert evaluate(readings(previous=previous, closed=closed)) is not None


def test_matching_reference_text_is_not_matching_identity():
    assert evaluate(readings(previous=("EXT/42",), closed=("ext/42",))) is not None


def test_zero_growth_at_zero_limits_is_clean():
    assert evaluate(readings(files=0, commits=0, max_files=0, max_commits=0)) is None


@pytest.mark.parametrize("files_limit,commits_limit", [(7, 5), (8, 2), (7, 2)])
def test_every_firing_arm_replays_its_original_readings(files_limit, commits_limit):
    alarm = evaluate(readings(max_files=files_limit, max_commits=commits_limit))
    assert alarm is not None
    stored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    assert (
        barren_tick_with_diff_growth(
            subject=stored.subject,
            readings=stored.readings,
            raised_at_sha=stored.raised_at_sha,
            raised_by=stored.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("value", ["bad-json", "null", "{}"])
def test_unreadable_input_is_not_a_clean_observation(slot, value):
    original = readings()
    altered = tuple(
        item.model_copy(update={"value": value}) if index == slot else item
        for index, item in enumerate(original)
    )
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(altered)
    assert raised.value.source_ref == original[slot].source_ref
    assert raised.value.signal == "barren_tick_with_diff_growth"
    assert isinstance(raised.value.__cause__, ValidationError)


@pytest.mark.parametrize("name", ["files", "commits", "max_files", "max_commits"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "3"])
def test_all_counts_are_nonnegative_integers(name, value):
    with pytest.raises(RunShapeReadError):
        evaluate(readings(**{name: value}))


@pytest.mark.parametrize("name", ["previous", "closed"])
@pytest.mark.parametrize("value", [("same", "same"), ("",), ("  ",), (1,)])
def test_reference_sets_require_unique_nonempty_opaque_keys(name, value):
    with pytest.raises(RunShapeReadError):
        evaluate(readings(**{name: value}))


@pytest.mark.parametrize("slot", [4, 5])
def test_substituted_config_field_is_refused(slot):
    original = readings()
    changed = tuple(
        item.model_copy(update={"source_ref": "other_field"}) if index == slot else item
        for index, item in enumerate(original)
    )
    with pytest.raises(RunShapeReadError, match="AppConfig"):
        evaluate(changed)


@pytest.mark.parametrize("size", [0, 5, 7])
def test_the_complete_reading_sequence_is_required(size):
    original = readings()
    altered = (*original, original[0]) if size == 7 else original[:size]
    with pytest.raises(RunShapeReadError, match="incomplete"):
        evaluate(altered)


def test_a_barren_tick_requires_a_lane_subject():
    with pytest.raises(RunShapeReadError, match="lane subject"):
        barren_tick_with_diff_growth(
            subject=AlarmSubject(kind=AlarmSubjectKind.SCOPE, scope_key="scope-a"),
            readings=readings(),
            raised_at_sha="sha",
            raised_by="holder",
        )


def test_closure_does_not_supply_unreadable_growth_counts():
    with pytest.raises(RunShapeReadError):
        evaluate(readings(closed=("EXT/42",), files=None))


def test_growth_limits_load_from_prefixed_environment(monkeypatch):
    monkeypatch.setenv("KODEZART_RUN_ALARM_BARREN_TICK_MAX_FILES_CHANGED", "17")
    monkeypatch.setenv("KODEZART_RUN_ALARM_BARREN_TICK_MAX_COMMITS_AHEAD", "13")
    config = AppConfig(_env_file=None)
    assert config.run_alarm_barren_tick_max_files_changed == 17
    assert config.run_alarm_barren_tick_max_commits_ahead == 13


@pytest.mark.parametrize("field", [FILES_FIELD, COMMITS_FIELD])
def test_negative_growth_limits_refuse_configuration(field):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, **{field: -1})
