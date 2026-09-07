"""Age is measured only by the run's recorded progress."""

import ast
import inspect
import json

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.domain import run_shape
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import escalation_ageing
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)

COMMIT_FIELD = "run_alarm_escalation_age_max_commits"
TICK_FIELD = "run_alarm_escalation_age_max_ticks"
SUBJECT = AlarmSubject(
    kind=AlarmSubjectKind.ESCALATION,
    scope_key="scope-a",
    lane_key="lane-a",
    issue_id="EXT/42",
    member_id="question-a",
)


def readings(
    *,
    commits=("before", "raised", "next", "last"),
    ticks=3,
    max_commits=1,
    max_ticks=5,
    resolved=False,
):
    escalation = {
        "issueId": "EXT/42",
        "escalationKey": "question-a",
        "raisedBy": "lane-holder",
        "raisedAtSha": "raised",
        "question": "A question?",
        "interimReading": "The recorded reading.",
        "interimBasis": "The recorded basis.",
    }
    return (
        AlarmReading(
            source_ref="comment/raise",
            value=json.dumps(escalation, indent=2),
            at_sha="raised",
        ),
        AlarmReading(
            source_ref="comment/raise",
            value=json.dumps(
                {
                    "state": "resolved" if resolved else "unresolved",
                    "decisionRef": "comment/decision" if resolved else None,
                }
            ),
        ),
        AlarmReading(
            source_ref="record/lane#commits",
            value=json.dumps(commits, indent=2),
            at_sha="last",
        ),
        AlarmReading(
            source_ref="record/walker#ticks-since-question", value=json.dumps(ticks)
        ),
        AlarmReading(source_ref=COMMIT_FIELD, value=json.dumps(max_commits)),
        AlarmReading(source_ref=TICK_FIELD, value=json.dumps(max_ticks)),
    )


def evaluate(values):
    return escalation_ageing(
        subject=SUBJECT,
        readings=values,
        raised_at_sha="observation-sha",
        raised_by="supervisor-holder",
    )


@pytest.mark.parametrize(
    "max_commits,max_ticks,field,configured,observed",
    [(1, 5, COMMIT_FIELD, 1, 2), (2, 2, TICK_FIELD, 2, 3), (1, 2, COMMIT_FIELD, 1, 2)],
)
def test_each_count_arm_names_its_actual_limit(
    max_commits, max_ticks, field, configured, observed
):
    observed_readings = readings(max_commits=max_commits, max_ticks=max_ticks)
    alarm = evaluate(observed_readings)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.ESCALATION_AGEING
    assert alarm.subject == SUBJECT
    assert alarm.bound == AlarmBound(
        config_field=field, configured_value=configured, observed_value=observed
    )
    assert alarm.readings == observed_readings
    assert alarm.raised_at_sha == "observation-sha"
    assert alarm.raised_by == "supervisor-holder"


@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("max_commits,max_ticks", [(2, 3), (3, 3), (2, 4), (100, 100)])
def test_equal_or_below_limits_stays_clean(resolved, max_commits, max_ticks):
    assert (
        evaluate(
            readings(resolved=resolved, max_commits=max_commits, max_ticks=max_ticks)
        )
        is None
    )


@pytest.mark.parametrize("max_commits,max_ticks", [(0, 0), (1, 5), (2, 2)])
def test_resolved_occurrence_is_clean_even_over_the_limits(max_commits, max_ticks):
    assert (
        evaluate(readings(resolved=True, max_commits=max_commits, max_ticks=max_ticks))
        is None
    )


def test_only_commits_after_the_recorded_position_count():
    values = readings(
        commits=("f" * 40, "0" * 40, "raised", "a", "b"),
        ticks=0,
        max_commits=1,
        max_ticks=0,
    )
    alarm = evaluate(values)
    assert alarm is not None
    assert alarm.bound.observed_value == 2


def test_no_commit_after_raise_and_no_tick_is_clean_at_zero_limits():
    assert (
        evaluate(
            readings(commits=("before", "raised"), ticks=0, max_commits=0, max_ticks=0)
        )
        is None
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_commits": 1, "max_ticks": 5},
        {"max_commits": 2, "max_ticks": 2},
        {"max_commits": 1, "max_ticks": 2},
    ],
)
def test_each_firing_arm_replays_from_its_own_durable_readings(overrides):
    alarm = evaluate(readings(**overrides))
    assert alarm is not None
    restored = RunAlarm.model_validate_json(alarm.model_dump_json(by_alias=True))
    assert (
        escalation_ageing(
            subject=restored.subject,
            readings=restored.readings,
            raised_at_sha=restored.raised_at_sha,
            raised_by=restored.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize(
    "commits", [(), ("other",), ("raised", "raised"), ("raised", "a", "a")]
)
def test_missing_or_ambiguous_history_refuses_observation(commits):
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(readings(commits=commits))
    assert raised.value.source_ref == "record/lane#commits"
    assert raised.value.signal == "escalation_ageing"


@pytest.mark.parametrize("commits", [(), ("other",), ("raised", "raised")])
def test_a_decision_does_not_make_an_unreadable_history_a_clean_observation(commits):
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(readings(commits=commits, resolved=True))
    assert raised.value.source_ref == "record/lane#commits"


def test_a_decision_does_not_supply_a_missing_tick_count():
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(readings(ticks=None, resolved=True))
    assert raised.value.source_ref == "record/walker#ticks-since-question"


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("value", ["not-json", "null", "{}"])
def test_malformed_readings_are_typed_errors_with_the_source(slot, value):
    original = readings()
    altered = tuple(
        item.model_copy(update={"value": value}) if position == slot else item
        for position, item in enumerate(original)
    )
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(altered)
    assert raised.value.source_ref == original[slot].source_ref
    assert isinstance(raised.value.__cause__, ValidationError)


@pytest.mark.parametrize("name", ["ticks", "max_commits", "max_ticks"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "2"])
def test_recorded_counts_are_nonnegative_integers(name, value):
    with pytest.raises(RunShapeReadError):
        evaluate(readings(**{name: value}))


@pytest.mark.parametrize("commits", [("raised", ""), ("raised", "  "), ("raised", 3)])
def test_commit_identities_are_nonempty_opaque_strings(commits):
    with pytest.raises(RunShapeReadError):
        evaluate(readings(commits=commits))


@pytest.mark.parametrize("size", [0, 1, 5, 7])
def test_exact_reading_set_is_required(size):
    values = readings()
    values = (*values, values[0]) if size == 7 else values[:size]
    with pytest.raises(RunShapeReadError, match="incomplete readings"):
        evaluate(values)


@pytest.mark.parametrize(
    "updates",
    [
        {"member_id": "other-question"},
        {"issue_id": "OTHER/1"},
        {"lane_key": None},
        {"kind": AlarmSubjectKind.SURFACE},
    ],
)
def test_subject_must_name_the_read_escalation_and_its_lane(updates):
    with pytest.raises(RunShapeReadError, match="subject"):
        escalation_ageing(
            subject=SUBJECT.model_copy(update=updates),
            readings=readings(),
            raised_at_sha="sha",
            raised_by="holder",
        )


@pytest.mark.parametrize("slot", [1, 4, 5])
def test_resolution_and_configuration_sources_cannot_be_substituted(slot):
    values = tuple(
        reading.model_copy(update={"source_ref": "some-other-source"})
        if index == slot
        else reading
        for index, reading in enumerate(readings())
    )
    with pytest.raises(RunShapeReadError):
        evaluate(values)


def test_age_limits_load_from_prefixed_environment(monkeypatch):
    monkeypatch.setenv("KODEZART_RUN_ALARM_ESCALATION_AGE_MAX_COMMITS", "7")
    monkeypatch.setenv("KODEZART_RUN_ALARM_ESCALATION_AGE_MAX_TICKS", "11")
    config = AppConfig(_env_file=None)
    assert config.run_alarm_escalation_age_max_commits == 7
    assert config.run_alarm_escalation_age_max_ticks == 11


@pytest.mark.parametrize("name", [COMMIT_FIELD, TICK_FIELD])
def test_negative_limits_refuse_configuration(name):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, **{name: -1})


def test_signal_module_is_pure_and_count_comparisons_have_no_literal_bound():
    tree = ast.parse(inspect.getsource(run_shape))
    allowed_imports = {
        "typing",
        "pydantic",
        "kodezart.domain.errors",
        "kodezart.types.domain.escalation",
        "kodezart.types.domain.run_alarm",
        "kodezart.types.domain.run_state",
    }
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports <= allowed_imports
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    forbidden_calls = {
        "open",
        "input",
        "exec",
        "eval",
        "__import__",
        "print",
        "breakpoint",
    }
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_calls
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(child, ast.Constant) and isinstance(child.value, (int, float))
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for child in ast.walk(node)
    )
