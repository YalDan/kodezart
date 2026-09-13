"""Audit cadence fields replace sampled mode with explicit coverage intervals."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


def test_audit_defaults_and_retired_fields():
    config = AppConfig()
    assert config.audit_sweep_interval_seconds == 3600.0
    assert config.audit_full_sweep_interval_seconds == 86400.0
    assert "audit_sweep_mode" not in AppConfig.model_fields
    assert "audit_sample_size" not in AppConfig.model_fields


@pytest.mark.parametrize("value", [60.0, 86400.0])
def test_bounds_load_through_environment(monkeypatch, value):
    monkeypatch.setenv("KODEZART_AUDIT_SWEEP_INTERVAL_SECONDS", str(value))
    monkeypatch.setenv("KODEZART_AUDIT_FULL_SWEEP_INTERVAL_SECONDS", str(value))
    config = AppConfig()
    assert config.audit_sweep_interval_seconds == value
    assert config.audit_full_sweep_interval_seconds == value


@pytest.mark.parametrize(
    "field", ["audit_sweep_interval_seconds", "audit_full_sweep_interval_seconds"]
)
@pytest.mark.parametrize("value", [59.0, 86401.0])
def test_invalid_bounds_refuse(field, value):
    with pytest.raises(ValidationError):
        AppConfig(**{field: value})


def test_full_interval_cannot_be_shorter_than_tick():
    with pytest.raises(ValidationError, match="must not be shorter"):
        AppConfig(
            audit_sweep_interval_seconds=120, audit_full_sweep_interval_seconds=60
        )
