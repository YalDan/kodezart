"""Explicit owner bounds and bindings reach their production consumer."""

import pytest
from pydantic import ValidationError

from kodezart.config.app import AppConfig
from kodezart.config.organize import OrganizeSettings
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_address import ScopeRef as LeafScopeRef
from tests.chains.test_organize_owner import factory, run_owner
from tests.prompts.test_organize_mandate_bindings import declared_operation


def test_scope_address_reexport_preserves_type_and_wire_shape():
    assert ScopeRef is LeafScopeRef
    ref = ScopeRef.model_validate({"kind": "issue", "key": "opaque/native-id"})
    assert ref.model_dump() == {"kind": "issue", "key": "opaque/native-id"}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"max_admission_rounds": 1},
        {"max_convergence_rounds": 1},
        {"max_admission_rounds": 0, "max_convergence_rounds": 2},
        {"max_admission_rounds": 2, "max_convergence_rounds": -1},
    ],
)
def test_bounds_are_both_required_and_positive(fields):
    with pytest.raises(ValidationError):
        OrganizeSettings.model_validate(fields)


@pytest.mark.parametrize("field", ["interval_seconds", "timeout_seconds"])
def test_the_organize_settings_carry_no_cadence(field: str) -> None:
    """The stages are no scheduled pass: a cadence field is refused, not read.

    Until 2026-09-24 the two fields scheduled an organize tick of their own.
    The stages run inside the scope run the heartbeat submits on the dispatch
    cadence, so a deployment still setting a cadence here is told so at load.
    """
    with pytest.raises(ValidationError, match="extra"):
        OrganizeSettings.model_validate(
            {"max_admission_rounds": 1, "max_convergence_rounds": 1, field: 900.0}
        )
    assert set(OrganizeSettings.model_fields) == {
        "max_admission_rounds",
        "max_convergence_rounds",
    }


def test_a_cadence_in_the_environment_is_refused_at_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS", "1")
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS", "1")
    monkeypatch.setenv("KODEZART_ORGANIZE__INTERVAL_SECONDS", "900")
    with pytest.raises(ValidationError, match="interval_seconds"):
        AppConfig()


@pytest.mark.parametrize(
    "change", ["missing_mandates", "undeclared_repository", "duplicate_scope"]
)
def test_scope_binding_has_one_declared_repository_and_no_ambiguous_authority(change):
    fields = declared_operation().model_dump()
    fields["organize_scopes"] = [
        {
            "scope": {"kind": "issue", "key": "parent"},
            "repo_url": fields["repos"][0]["url"],
        }
    ]
    if change == "missing_mandates":
        fields["organize_mandates"] = []
    elif change == "undeclared_repository":
        fields["organize_scopes"][0]["repo_url"] = "https://another.invalid/repo"
    else:
        fields["organize_scopes"] *= 2
    with pytest.raises(ValidationError):
        OperationConfig.model_validate(fields)


async def test_environment_bounds_reach_the_owner_and_stop_after_one_author(
    monkeypatch,
):
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS", "1")
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS", "7")
    monkeypatch.setenv("KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS", "4")
    config = AppConfig()
    assert config.organize.max_admission_rounds == 1
    assert config.organize.max_convergence_rounds == 7
    owner, _board, executor = factory(settings=config, refuse_forever=True)

    report = await run_owner(owner)

    assert report.halt is not None
    assert report.halt.cause.value == "admission_exhausted"
    assert report.halt.bound.model_dump() == {
        "setting": "organize.max_admission_rounds",
        "value": 1,
        "rounds_used": 1,
        "loop": "admission",
    }
    assert (
        len(
            [
                call
                for call in executor.calls
                if call["output_format"]["schema"].get("title") == "OrganizeProposal"
            ]
        )
        == 1
    )


def test_declared_owner_bindings_without_bounds_refuse_construction():
    with pytest.raises(OperationMemberAbsentError, match="organize"):
        factory(settings=AppConfig(organize=None))
