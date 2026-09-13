"""Validated logging choices preserve sources and actual native behavior."""

import json
import logging

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.logging import configure_logging
from tests.core.test_logging_chain import configured_chain
from tests.core.test_retired_config import _from_source


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_logging_sources_reach_the_native_level_and_renderer(
    source, tmp_path, monkeypatch
):
    value = {"level": "warning", "pretty": True}
    if source == "init":
        config = AppConfig(_env_file=None, logging=value)
    elif source == "env":
        monkeypatch.setenv("KODEZART_LOGGING__LEVEL", "warning")
        monkeypatch.setenv("KODEZART_LOGGING__PRETTY", "true")
        config = AppConfig(_env_file=None)
    elif source == "dotenv":
        dotenv = tmp_path / ".env"
        dotenv.write_text(
            "KODEZART_LOGGING__LEVEL=warning\nKODEZART_LOGGING__PRETTY=true\n"
        )
        config = AppConfig(_env_file=dotenv)
    else:
        (tmp_path / "KODEZART_LOGGING").write_text(json.dumps(value))
        config = AppConfig(_env_file=None, _secrets_dir=tmp_path)
    assert config.logging.model_dump() == value
    with configured_chain() as output:
        configure_logging(log_level=config.logging.level, pretty=config.logging.pretty)
        logging.getLogger("fixture").info("below_threshold")
        logging.getLogger("fixture").warning("retained_warning")
        text = output.getvalue()
        assert logging.getLogger().level == logging.WARNING
        assert "below_threshold" not in text
        assert "retained_warning" in text and "\x1b[" in text


@pytest.mark.parametrize("level", list(logging.getLevelNamesMapping()))
@pytest.mark.parametrize("lowercase", [False, True])
def test_all_standard_level_names_and_aliases_keep_native_meaning(level, lowercase):
    configured = level.lower() if lowercase else level
    config = AppConfig(_env_file=None, logging={"level": configured})
    with configured_chain():
        configure_logging(log_level=config.logging.level, pretty=config.logging.pretty)
        assert logging.getLogger().level == logging.getLevelNamesMapping()[level]


@pytest.mark.parametrize("value", ["typo", "INFO ", "INFO\n", "", "20"])
def test_unknown_level_refuses_instead_of_silently_selecting_info(value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, logging={"level": value})


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize(
    "field,value", [("log_level", "warning"), ("log_pretty", "true")]
)
def test_old_flat_logging_names_refuse(source, field, value, tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, value, tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


def test_logging_defaults_and_source_precedence(tmp_path, monkeypatch):
    assert AppConfig(_env_file=None).logging.model_dump() == {
        "level": "INFO",
        "pretty": False,
    }
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_LOGGING").write_text('{"level":"ERROR","pretty":true}')
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_LOGGING__LEVEL=WARNING\n")
    monkeypatch.setenv("KODEZART_LOGGING__LEVEL", "DEBUG")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert (
        AppConfig(**kwargs, logging={"level": "CRITICAL"}).logging.level == "CRITICAL"
    )
    assert AppConfig(**kwargs).logging.level == "DEBUG"
    monkeypatch.delenv("KODEZART_LOGGING__LEVEL")
    assert AppConfig(**kwargs).logging.level == "WARNING"
    assert AppConfig(_env_file=None, _secrets_dir=secrets).logging.level == "ERROR"


@pytest.mark.parametrize("value", [{"prety": True}, {"pretty": "invalid"}])
def test_invalid_logging_section_refuses(value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, logging=value)
