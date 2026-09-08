"""Removed settings fail clearly at the config boundary."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


def _from_source(source, field, value, tmp_path, monkeypatch):
    name = "KODEZART_" + field.upper()
    if source == "init":
        return AppConfig(_env_file=None, **{field: value})
    if source == "env":
        monkeypatch.setenv(name, value)
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(f"{name}={value}\n")
        return AppConfig(_env_file=path)
    (tmp_path / name).write_text(value)
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
