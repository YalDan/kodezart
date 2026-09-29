"""Removed settings fail clearly at the config boundary."""

import json

import pytest
from pydantic import ValidationError

from kodezart.config.app import RETIRED_TO_SUPERVISOR_PASS, AppConfig


def _from_source(source, field, value, tmp_path, monkeypatch):
    name = "KODEZART_" + field.upper()
    if source == "init":
        return AppConfig(_env_file=None, **{field: value})
    encoded = value if isinstance(value, str) else json.dumps(value)
    if source == "env":
        monkeypatch.setenv(name, encoded)
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(f"{name}={encoded}\n")
        return AppConfig(_env_file=path)
    (tmp_path / name).write_text(encoded)
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)


@pytest.mark.parametrize(
    "field",
    [
        "organize_max_admission_rounds",
        "organize_max_convergence_rounds",
        "union_check_cleanup_poll_interval_seconds",
        # The prompt passes' deterministic gate is gone: the gate is an agent
        # question, and a signal list for it is a setting nothing reads.
        "fire_prep_pass_gate_signals",
        "grooming_pass_gate_signals",
        # The v0.2 dispatcher and the scope cron both run on the dispatch
        # cadence pair; there is no switch between them any more.
        "dispatch_workflow",
    ],
)
@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_removed_setting_refuses_all_supported_sources(
    source, field, tmp_path, monkeypatch
):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, "7", tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


#: The run-alarm bounds only the supervisor's code observers read. Those
#: observers were merged into the supervisor pass, a session that judges the
#: same conduct from the board, so each name is refused at boot with a
#: message naming that pass, never silently ignored.
SUPERVISOR_PASS_REPLACED = (
    "run_alarm_max_commits_without_closure",
    "run_alarm_escalation_age_max_commits",
    "run_alarm_escalation_age_max_ticks",
)


def test_the_retired_run_alarm_bounds_are_exactly_the_three_the_observers_read():
    assert frozenset(SUPERVISOR_PASS_REPLACED) == RETIRED_TO_SUPERVISOR_PASS
    assert RETIRED_TO_SUPERVISOR_PASS.isdisjoint(AppConfig.model_fields)


@pytest.mark.parametrize("field", SUPERVISOR_PASS_REPLACED)
@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_a_bound_the_supervisor_pass_replaced_is_refused_naming_that_pass(
    source, field, tmp_path, monkeypatch
):
    with pytest.raises(ValidationError) as caught:
        _from_source(source, field, "7", tmp_path, monkeypatch)
    refusal = str(caught.value)
    assert field in refusal.casefold()
    assert "the supervisor pass" in refusal
    assert "KODEZART_SUPERVISOR_PASS_INTERVAL_SECONDS" in refusal
    assert "judges this from the board" in refusal
    assert "input_value" not in refusal


def test_an_unprefixed_name_of_a_replaced_bound_is_not_refused(monkeypatch):
    monkeypatch.setenv("RUN_ALARM_MAX_COMMITS_WITHOUT_CLOSURE", "7")
    assert AppConfig(_env_file=None).git.remote == "origin"


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_retained_remote_override_loads_at_the_same_config_boundary(
    source, tmp_path, monkeypatch
):
    config = _from_source(source, "git", {"remote": "upstream"}, tmp_path, monkeypatch)
    assert config.git.remote == "upstream"


def test_default_config_exposes_no_removed_setting():
    config = AppConfig(_env_file=None)
    assert config.git.remote == "origin"
    assert "organize_max_admission_rounds" not in config.model_dump()
    assert "organize_max_convergence_rounds" not in config.model_dump()
    assert "union_check_cleanup_poll_interval_seconds" not in config.model_dump()
    for field in SUPERVISOR_PASS_REPLACED:
        assert field not in config.model_dump()


@pytest.mark.parametrize("source", ["env", "secret"])
@pytest.mark.parametrize(
    "name",
    [
        "organize_max_admission_rounds",
        "organize_max_convergence_rounds",
        "knowledge_mcp_token",
        "union_check_cleanup_poll_interval_seconds",
    ],
)
def test_unrelated_unprefixed_names_are_not_retired_settings(
    source, name, tmp_path, monkeypatch
):
    if source == "env":
        monkeypatch.setenv(name, "synthetic-unrelated-value")
        config = AppConfig(_env_file=None)
    else:
        (tmp_path / name).write_text("synthetic-unrelated-value")
        config = AppConfig(_env_file=None, _secrets_dir=tmp_path)
    assert config.git.remote == "origin"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aggregate_count_token_distance", 7),
        ("aggregate_identifier_roster_min_length", 5),
        ("aggregate_tracker_object_nouns", ["issues"]),
        ("aggregate_issue_identifier_pattern", "WORK/[0-9]+"),
        ("aggregate_identifier_separator_pattern", "~"),
    ],
)
@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_removed_aggregate_grammar_refuses_previously_valid_settings(
    source, field, value, tmp_path, monkeypatch
):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, value, tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize("source", ["env", "secret"])
def test_aggregate_retirement_is_an_exact_name_not_a_prefix_rule(
    source, tmp_path, monkeypatch
):
    name = "KODEZART_AGGREGATE_COUNT_TOKEN_DISTANCE_ARCHIVE"
    if source == "env":
        monkeypatch.setenv(name, "synthetic-unrelated-value")
        config = AppConfig(_env_file=None)
    else:
        (tmp_path / name).write_text("synthetic-unrelated-value")
        config = AppConfig(_env_file=None, _secrets_dir=tmp_path)
    assert config.git.remote == "origin"


def test_a_retired_aggregate_secret_is_rejected_without_reading_its_value(
    tmp_path, monkeypatch
):
    from pathlib import Path

    path = tmp_path / "KODEZART_AGGREGATE_TRACKER_OBJECT_NOUNS"
    path.write_text("synthetic-secret-value")
    original = Path.read_text

    def read_text(self, *args, **kwargs):
        assert self != path, "A retired secret has no value consumer"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        AppConfig(_env_file=None, _secrets_dir=tmp_path)
    assert "synthetic-secret-value" not in str(caught.value)


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("deny_patterns", {"credentials": []}),
        ("deny_pattern_verdicts", {"credentials": "clean"}),
    ],
)
def test_removed_deny_policy_refuses_previously_valid_overrides(
    source, field, value, tmp_path, monkeypatch
):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, value, tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)
