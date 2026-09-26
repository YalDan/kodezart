"""Age is measured only by the run's recorded progress."""

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

import kodezart.domain
from kodezart.config.app import AppConfig
from kodezart.domain import (
    escalation_age_record,
    lane_alarms,
    mandate_graph,
    run_alarm_record,
    run_alarm_table,
    run_shape,
    stream_signals,
    tally_record,
)
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import escalation_ageing
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubjectKind,
    CountEvidence,
    EscalationEvidence,
    EscalationSubject,
    ReferencesEvidence,
    ResolutionEvidence,
    RunAlarm,
    TextEvidence,
)
from kodezart.types.domain.run_state import LaneEscalation

COMMIT_FIELD = "run_alarm_escalation_age_max_commits"
TICK_FIELD = "run_alarm_escalation_age_max_ticks"
SUBJECT = EscalationSubject(
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
            value=EscalationEvidence(value=LaneEscalation.model_validate(escalation)),
            at_sha="raised",
        ),
        AlarmReading(
            source_ref="comment/raise",
            value=ResolutionEvidence(
                value=EscalationResolution(
                    state=EscalationResolutionState.RESOLVED
                    if resolved
                    else EscalationResolutionState.UNRESOLVED,
                    decision_ref="comment/decision" if resolved else None,
                )
            ),
        ),
        AlarmReading(
            source_ref="record/lane#commits",
            value=ReferencesEvidence(value=commits),
            at_sha="last",
        ),
        AlarmReading(
            source_ref="record/walker#ticks-since-question",
            value=CountEvidence(value=ticks),
        ),
        AlarmReading(source_ref=COMMIT_FIELD, value=CountEvidence(value=max_commits)),
        AlarmReading(source_ref=TICK_FIELD, value=CountEvidence(value=max_ticks)),
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
    with pytest.raises(ValidationError):
        evaluate(readings(ticks=None, resolved=True))


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("value", ["not-json", "null", "{}"])
def test_wrong_evidence_arm_is_a_typed_error_with_the_source(slot, value):
    original = readings()
    altered = tuple(
        item.model_copy(update={"value": TextEvidence(value=value)})
        if position == slot
        else item
        for position, item in enumerate(original)
    )
    with pytest.raises(RunShapeReadError) as raised:
        evaluate(altered)
    assert raised.value.source_ref == original[slot].source_ref
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("name", ["ticks", "max_commits", "max_ticks"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "2"])
def test_recorded_counts_are_nonnegative_integers(name, value):
    with pytest.raises(ValidationError):
        evaluate(readings(**{name: value}))


@pytest.mark.parametrize("commits", [("raised", ""), ("raised", "  "), ("raised", 3)])
def test_commit_identities_are_nonempty_opaque_strings(commits):
    with pytest.raises(ValidationError):
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


#: Every domain module that names an alarm signal, with the exact imports it
#: may make. The set is not chosen by hand: the test below derives it from
#: the package and requires this table to cover exactly that.
SIGNAL_MODULES = [
    (
        run_shape,
        {
            "typing",
            "pydantic",
            "kodezart.domain.errors",
            "kodezart.domain.run_alarm_record",
            "kodezart.types.domain.escalation",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_state",
            "kodezart.types.domain.surface",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.organize",
        },
    ),
    # The record rule is the same kind of module: arithmetic over readings
    # the caller supplies, so it is held to the same import set and the
    # same no-literal-bound rule as the signal it composes for.
    (
        tally_record,
        {
            "collections.abc",
            "kodezart.domain.fire_plateau",
            "kodezart.domain.run_alarm_table",
            "kodezart.domain.run_shape",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_state",
            "kodezart.types.domain.tracker",
        },
    ),
    # The two signals over a criterion's own state and its lane's account
    # of it: the same arithmetic over readings a caller supplies, so the
    # same import set and the same no-literal-bound rule.
    (
        stream_signals,
        {
            "collections.abc",
            "kodezart.domain.gap",
            "kodezart.domain.run_shape",
            "kodezart.types.domain.node_session",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_event",
            "kodezart.types.domain.tracker",
        },
    ),
    # What one lane's observation composes at every address it owns. It
    # holds the folds rather than being one, and it composes no count of
    # its own, so the same two rules apply to it unchanged.
    (
        lane_alarms,
        {
            "collections.abc",
            "dataclasses",
            "kodezart.domain.run_alarm_record",
            "kodezart.domain.run_alarm_table",
            "kodezart.domain.run_event_stream",
            "kodezart.domain.stream_signals",
            "kodezart.domain.tally_record",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_event",
            "kodezart.types.domain.run_state",
            "kodezart.types.domain.tracker",
        },
    ),
    # The table every fold is reached through, and the one replay of a stored
    # record: it holds the folds and their declared scans, and no count.
    (
        run_alarm_table,
        {
            "collections.abc",
            "dataclasses",
            "typing",
            "kodezart.domain.errors",
            "kodezart.domain.mandate_graph",
            "kodezart.domain.run_shape",
            "kodezart.domain.stream_signals",
            "kodezart.types.domain.dispatch",
            "kodezart.types.domain.run_alarm",
        },
    ),
    # The two landed folds over a lane's decisions and its milestone graph:
    # the same kind of module, held to the same rules.
    (
        mandate_graph,
        {
            "collections.abc",
            "kodezart.domain.run_shape",
            "kodezart.types.domain.agent",
            "kodezart.types.domain.mandate_graph",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.tracker",
        },
    ),
    # The record's address and its one-listing read: it names every signal
    # because an address is composed from one, and it holds no count at all.
    (
        run_alarm_record,
        {
            "collections.abc",
            "pydantic",
            "kodezart.domain.comment_markers",
            "kodezart.domain.errors",
            "kodezart.domain.run_event_stream",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_event",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.surface",
            "kodezart.types.domain.tracker",
        },
    ),
    # What the one ageing record at an open question's address says next:
    # arithmetic over the lane orders and the stored record it is handed, so
    # the same import set and the same no-literal-bound rule.
    (
        escalation_age_record,
        {
            "collections.abc",
            "dataclasses",
            "kodezart.domain.errors",
            "kodezart.domain.run_shape",
            "kodezart.types.domain.escalation",
            "kodezart.types.domain.run_alarm",
        },
    ),
]


@pytest.mark.parametrize(("module", "allowed_imports"), SIGNAL_MODULES)
def test_signal_module_is_pure_and_count_comparisons_have_no_literal_bound(
    module, allowed_imports
):
    tree = ast.parse(inspect.getsource(module))
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
    # Arithmetic over values it was handed has nothing to await. With the
    # import allow-list an await cannot by itself reach I/O, but "performs no
    # I/O" is held directly by there being nothing here that could wait.
    assert not any(
        isinstance(node, (ast.Await, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith))
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(child, ast.Constant) and isinstance(child.value, (int, float))
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for child in ast.walk(node)
    )


def _names_an_alarm_signal(source: str) -> bool:
    """Whether *source* names the signal vocabulary in any spelling.

    An import of the name, a bare use of it, and an attribute read of it off
    a module imported whole (``run_alarm.AlarmSignal``) all count.
    """
    tree = ast.parse(source)
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "AlarmSignal" for alias in node.names)
        )
        or (isinstance(node, ast.Name) and node.id == "AlarmSignal")
        or (isinstance(node, ast.Attribute) and node.attr == "AlarmSignal")
        for node in ast.walk(tree)
    )


def _modules_naming_an_alarm_signal(root: Path, package: str) -> set[str]:
    """Every module under *root*, at any depth, that names the vocabulary."""
    found = set()
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if _names_an_alarm_signal(path.read_text(encoding="utf-8")):
            found.add(".".join((package, *parts)))
    return found


def test_every_domain_module_naming_an_alarm_signal_is_in_the_purity_scan():
    """The scanned surface is derived from the package, never listed by hand.

    A new signal module that names the vocabulary and is missing from the
    table above would otherwise carry none of its protection: no import
    allow-list, no await, no literal bound. Derived from every module under
    the package, subpackages included, so a module added anywhere under it is
    found.
    """
    derived = _modules_naming_an_alarm_signal(
        Path(kodezart.domain.__path__[0]), kodezart.domain.__name__
    )
    scanned = {module.__name__ for module, _ in SIGNAL_MODULES}

    # Not vacuous: the fold modules are found by the derivation itself.
    assert {run_shape.__name__, stream_signals.__name__} <= derived
    assert scanned == derived


def test_the_derivation_finds_a_subpackage_module_reading_the_vocabulary_by_attribute(
    tmp_path,
):
    """The derivation's own positive control, on a tree written for it.

    The module sits in a subpackage and never imports the name: it imports
    the vocabulary's module whole and reads the name off it.
    """
    nested = tmp_path / "folds" / "deeper"
    nested.mkdir(parents=True)
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "folds" / "__init__.py").write_text("", encoding="utf-8")
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "quiet.py").write_text("VALUE = 1\n", encoding="utf-8")
    (nested / "attribute_fold.py").write_text(
        "from kodezart.types.domain import run_alarm\n"
        "\n"
        "SIGNAL = run_alarm.AlarmSignal.TALLY_UNMOVED\n",
        encoding="utf-8",
    )

    assert _modules_naming_an_alarm_signal(tmp_path, "synthetic") == {
        "synthetic.folds.deeper.attribute_fold"
    }
