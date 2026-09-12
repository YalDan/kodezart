"""Explicit owner bounds and bindings reach their production consumer."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.organize_settings import OrganizeSettings
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    RunKind,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_address import ScopeRef as LeafScopeRef
from tests.chains.test_organize_owner import factory
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


async def test_environment_bounds_reach_the_tick_and_stop_after_one_author(
    monkeypatch,
):
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS", "1")
    monkeypatch.setenv("KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS", "7")
    config = AppConfig()
    assert config.organize.max_admission_rounds == 1
    assert config.organize.max_convergence_rounds == 7
    tick, board, executor = factory(tick=True, settings=config, refuse_forever=True)
    from kodezart.domain.errors import OrganizeWriteRefusalError

    instant = datetime(2026, 9, 12, tzinfo=UTC)
    with pytest.raises(OrganizeWriteRefusalError, match="admission_exhausted"):
        await tick.run(instant)
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
    identity = RunIdentity(
        kind=RunKind.GROOMING, name="grooming_pass", started_at=instant
    )
    assert any(
        identity.title() in str(args.get("body", ""))
        for name, args in board.calls
        if name == "save_comment"
    )


async def test_actual_tick_uses_fresh_remote_trunk_and_existing_run_identity():
    tick, board, executor = factory(tick=True)
    instant = datetime(2026, 9, 12, tzinfo=UTC)
    assert await tick.run(instant) is PassRun.RAN
    assert all(
        f"<base_ref>{'a' * 40}</base_ref>" in call["prompt"] for call in executor.calls
    )
    identity = RunIdentity(
        kind=RunKind.GROOMING, name="grooming_pass", started_at=instant
    )
    assert any(
        identity.title() in str(args.get("body", ""))
        for name, args in board.calls
        if name == "save_comment"
    )


def test_declared_owner_bindings_without_bounds_refuse_construction():
    with pytest.raises(OperationMemberAbsentError, match="organize"):
        factory(tick=True, settings=AppConfig(organize=None))
