"""The configured write-lease duration stays distinct from fire claims."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig

LEASE_ENV = "KODEZART_TRACKER_SURFACE_LEASE_SECONDS"


def test_surface_lease_uses_its_prefixed_environment_override(monkeypatch) -> None:
    monkeypatch.setenv(LEASE_ENV, "321.5")
    monkeypatch.setenv("KODEZART_TRACKER_CLAIM_LEASE_SECONDS", "456")
    config = AppConfig()

    assert config.tracker_surface_lease_seconds == 321.5
    assert config.tracker_claim_lease_seconds == 456.0


@pytest.mark.parametrize("value", ["60", "86400"])
def test_surface_lease_accepts_the_declared_inclusive_bounds(monkeypatch, value):
    monkeypatch.setenv(LEASE_ENV, value)
    assert AppConfig().tracker_surface_lease_seconds == float(value)


@pytest.mark.parametrize("value", ["0", "59.9", "86400.1", "inf", "nan"])
def test_surface_lease_rejects_invalid_duration_at_configuration(monkeypatch, value):
    monkeypatch.setenv(LEASE_ENV, value)
    with pytest.raises(ValidationError, match="tracker_surface_lease_seconds"):
        AppConfig()
