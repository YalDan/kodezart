"""The native Audit roster and required operator values are explicit."""

import pytest
from pydantic import ValidationError

from kodezart.composition.audit import verify_audit_configuration
from kodezart.core.config import AppConfig
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    RunKind,
)
from tests.integration.test_audit_scheduler import dependencies


@pytest.mark.parametrize("value", [None, 0, -1, float("inf"), float("nan")])
def test_configured_audit_has_no_implicit_or_nonpositive_timeout(value):
    with pytest.raises(ValidationError):
        AppConfig(
            _env_file=None, audit={} if value is None else {"timeout_seconds": value}
        )


def test_nested_audit_environment_preserves_actual_operator_timeout(monkeypatch):
    monkeypatch.setenv("KODEZART_AUDIT__TIMEOUT_SECONDS", "431")
    config = AppConfig(_env_file=None)
    assert config.audit.timeout_seconds == 431
    assert config.write_back is None


@pytest.mark.parametrize(
    "damage", ["duplicate", "different_repository", "missing_destination"]
)
def test_audit_roster_has_no_ambiguous_or_incomplete_binding(damage):
    _config, operation, *_ = dependencies()
    fields = operation.model_dump()
    if damage == "duplicate":
        fields["audit_scopes"] *= 2
    elif damage == "different_repository":
        fields["audit_scopes"][0]["repo_url"] = (
            "https://unconfigured.invalid/repository"
        )
    else:
        del fields["audit_scopes"][0]["report_issue_key"]
    with pytest.raises(ValidationError):
        OperationConfig.model_validate(fields)


def test_declared_audit_generic_sink_refuses_until_verified_record_seam_exists():
    config, operation, _server, tracker, forge = dependencies()
    fields = operation.model_dump()
    fields["records"][RunKind.AUDIT.value] = {
        "system": "tracker",
        "name": "actual-audit-sink",
        "id": "FIX-1",
        "append_only": True,
    }
    operation = OperationConfig.model_validate(fields)
    with pytest.raises(OperationMemberAbsentError, match=r"records\.audit"):
        verify_audit_configuration(
            config=config, operation=operation, tracker=tracker, forge=forge
        )
