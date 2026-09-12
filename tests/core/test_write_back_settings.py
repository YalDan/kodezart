"""The canonical verification budget is explicit and validated."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


def test_undeclared_write_back_budget_is_absent():
    assert AppConfig(_env_file=None).write_back is None


@pytest.mark.parametrize("value", [0, 11, 1.5])
def test_invalid_write_back_budget_refuses_at_settings_boundary(value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, write_back={"max_verify_rounds": value})


def test_declared_write_back_budget_has_no_default():
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, write_back={})


@pytest.mark.parametrize("value", [1, 10])
def test_valid_budget_boundaries_and_json_serialization(value):
    config = AppConfig(_env_file=None, write_back={"max_verify_rounds": value})
    assert config.write_back.model_dump() == {"max_verify_rounds": value}


def test_retired_flat_environment_name_refuses(monkeypatch):
    monkeypatch.setenv("KODEZART_WRITE_BACK_MAX_VERIFY_ROUNDS", "3")
    with pytest.raises(ValidationError, match="write_back_max_verify_rounds"):
        AppConfig(_env_file=None)
