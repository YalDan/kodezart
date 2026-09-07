"""Scope terminal values preserve the facts a wire consumer must inspect."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope_terminal import (
    ScopeRecordKind,
    ScopeResidual,
    ScopeResidualClass,
    ScopeResidualItem,
    ScopeResidualOwner,
    ScopeResidualOwnerKind,
    ScopeStoppingRule,
)


@pytest.mark.parametrize(
    "name",
    ["scope_converged", "scope_converged_with_residual", "scope_stopped_short"],
)
def test_scope_outcomes_extend_the_existing_workflow_vocabulary(name: str) -> None:
    member = WorkflowOutcome[name]
    assert member.name == name
    assert member.value == name
    assert list(WorkflowOutcome).index(member) > list(WorkflowOutcome).index(
        WorkflowOutcome.shutdown_abandoned
    )


@pytest.mark.parametrize(
    "name",
    [
        "UNADMITTED_ISSUE",
        "UNCONVERGED_DEFECT_CLASS",
        "OPEN_ESCALATION",
        "FAILING_CRITERION",
        "LANE_WITHOUT_OPEN_PR",
        "UNRECORDED_AT_TERMINAL",
    ],
)
def test_residual_classes_are_named_obligations(name: str) -> None:
    assert ScopeResidualClass[name].value == name.lower()


def _item(
    kind: ScopeRecordKind = ScopeRecordKind.CRITERION_SUB_ISSUE,
    owner: ScopeResidualOwnerKind = ScopeResidualOwnerKind.OPERATOR,
) -> ScopeResidualItem:
    return ScopeResidualItem(
        issue_id="EXT/42",
        residual_class=ScopeResidualClass.FAILING_CRITERION,
        record_kind=kind,
        record_ref="opaque record / 73",
        detail="The acceptance check needs access to a private test account.",
        act="Run the account-access check and record its evidence.",
        owner=ScopeResidualOwner(kind=owner, key="release operator / alpha"),
    )


@pytest.mark.parametrize("kind", list(ScopeRecordKind))
@pytest.mark.parametrize("owner", list(ScopeResidualOwnerKind))
def test_residual_roundtrip_retains_record_kind_act_and_owner(
    kind: ScopeRecordKind, owner: ScopeResidualOwnerKind
) -> None:
    item = _item(kind, owner)
    residual = ScopeResidual(items=(item,), stopping_rule=None)
    wire = residual.model_dump(mode="json", by_alias=True)
    assert wire == {
        "items": [
            {
                "issueId": "EXT/42",
                "residualClass": "failing_criterion",
                "recordKind": kind.value,
                "recordRef": "opaque record / 73",
                "detail": item.detail,
                "act": item.act,
                "owner": {"kind": owner.value, "key": "release operator / alpha"},
            }
        ],
        "stoppingRule": None,
    }
    assert ScopeResidual.model_validate_json(residual.model_dump_json()) == residual


@pytest.mark.parametrize(
    "missing",
    ["issueId", "residualClass", "recordKind", "recordRef", "detail", "act", "owner"],
)
def test_residual_item_cannot_omit_handoff_facts(missing: str) -> None:
    raw = _item().model_dump(mode="json", by_alias=True)
    del raw[missing]
    with pytest.raises(ValidationError):
        ScopeResidualItem.model_validate(raw)


def test_residual_ownership_is_queryable_without_parsing_prose() -> None:
    residual = ScopeResidual(
        items=tuple(_item(owner=owner) for owner in ScopeResidualOwnerKind),
        stopping_rule=None,
    )
    operator_obligations = [
        item
        for item in residual.items
        if item.residual_class is ScopeResidualClass.FAILING_CRITERION
        and item.owner.kind is ScopeResidualOwnerKind.OPERATOR
    ]
    assert operator_obligations == [_item()]


def test_stopping_rule_roundtrip_retains_the_producer_bound() -> None:
    rule = ScopeStoppingRule(
        config_field="organize_max_admission_rounds", configured_value=4, rounds_used=4
    )
    assert rule.model_dump(mode="json", by_alias=True) == {
        "configField": "organize_max_admission_rounds",
        "configuredValue": 4,
        "roundsUsed": 4,
    }


def test_residual_cannot_be_mutated_through_a_callers_collection() -> None:
    source = [_item()]
    residual = ScopeResidual.model_validate({"items": source, "stopping_rule": None})
    source.clear()
    assert residual.items == (_item(),)
    with pytest.raises(ValidationError, match="frozen"):
        residual.items = ()
    with pytest.raises(ValidationError, match="frozen"):
        residual.items[0].act = "Silently change the handoff."
    with pytest.raises(ValidationError, match="frozen"):
        residual.items[0].owner.key = "a different operator"
