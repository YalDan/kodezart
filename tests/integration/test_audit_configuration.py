"""The native Audit roster and required operator values are explicit."""

import pytest
from pydantic import ValidationError

from kodezart.composition.audit import verify_audit_configuration
from kodezart.config.app import AppConfig
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
    "damage",
    [
        "duplicate",
        "different_repository",
        "blank_destination",
        "whitespace_destination",
        "missing_destination",
    ],
)
def test_audit_roster_has_no_ambiguous_or_incomplete_binding(damage):
    """The one scope table refuses an ambiguous row at load, an incomplete one
    at the audit's own composition.

    Where a row's shape is a fact about the table — one scope declared twice, a
    repository nothing declares, a destination present but empty or blank — the
    file does not load. Omitting the destination is different: it is a legal
    table for a deployment with no audit, so it loads and the configured audit
    refuses it by the member's own name, before any backend call.
    """
    config, operation, _server, tracker, forge = dependencies()
    fields = operation.model_dump()
    if damage == "duplicate":
        fields["organize_scopes"] *= 2
    elif damage == "different_repository":
        fields["organize_scopes"][0]["repo_url"] = (
            "https://unconfigured.invalid/repository"
        )
    elif damage == "blank_destination":
        fields["organize_scopes"][0]["report_issue_key"] = ""
    elif damage == "whitespace_destination":
        fields["organize_scopes"][0]["report_issue_key"] = " "
    else:
        del fields["organize_scopes"][0]["report_issue_key"]
        loaded = OperationConfig.model_validate(fields)
        assert loaded.organize_scopes[0].report_issue_key is None
        with pytest.raises(OperationMemberAbsentError) as refused:
            verify_audit_configuration(
                config=config, operation=loaded, tracker=tracker, forge=forge
            )
        assert refused.value.missing == "organize_scopes.report_issue_key"
        return
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


def test_an_audit_operation_declaring_no_protection_record_prefix_is_refused():
    """The drift arm reads decision records; an operation that cannot address
    them is refused by the member's own name, before any backend call.
    """
    config, operation, server, tracker, forge = dependencies()
    fields = operation.model_dump()
    del fields["marker_prefixes"]["ruling"]
    loaded = OperationConfig.model_validate(fields)
    with pytest.raises(OperationMemberAbsentError) as refused:
        verify_audit_configuration(
            config=config, operation=loaded, tracker=tracker, forge=forge
        )
    assert refused.value.missing == "marker_prefixes['ruling']"
    assert server.calls == []
