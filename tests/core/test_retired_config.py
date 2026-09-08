"""Removed settings fail clearly at the config boundary."""

import json

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


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


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_retained_remote_override_loads_at_the_same_config_boundary(
    source, tmp_path, monkeypatch
):
    config = _from_source(source, "git_remote", "upstream", tmp_path, monkeypatch)
    assert config.git_remote == "upstream"


def test_default_config_exposes_no_removed_setting():
    config = AppConfig(_env_file=None)
    assert config.git_remote == "origin"
    assert "organize_max_admission_rounds" not in config.model_dump()
    assert "organize_max_convergence_rounds" not in config.model_dump()
    assert "union_check_cleanup_poll_interval_seconds" not in config.model_dump()


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
    assert config.git_remote == "origin"


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
    assert config.git_remote == "origin"


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
