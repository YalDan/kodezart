"""Both independent organize bounds are deployment settings."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


@pytest.mark.parametrize(
    "field", ["organize_max_admission_rounds", "organize_max_convergence_rounds"]
)
@pytest.mark.parametrize("value", [1, 10])
def test_organize_bound_has_integer_type_and_env_binding(monkeypatch, field, value):
    monkeypatch.setenv("KODEZART_" + field.upper(), str(value))
    assert AppConfig.model_fields[field].annotation is int
    assert getattr(AppConfig(_env_file=None), field) == value


@pytest.mark.parametrize(
    "field", ["organize_max_admission_rounds", "organize_max_convergence_rounds"]
)
@pytest.mark.parametrize("value", [0, 11, 1.5])
def test_organize_bound_rejects_outside_or_fractional_values(field, value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, **{field: value})
