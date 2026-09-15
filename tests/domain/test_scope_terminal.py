"""Retired terminal wrappers do not remove already public outcome values."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope_terminal import (
    BLOCKING_RESIDUAL_CLASSES,
    ScopeRecordRef,
    ScopeResidual,
    ScopeResidualClass,
    ScopeResidualItem,
    ScopeResidualOwner,
    ScopeResidualOwnerKind,
)
from kodezart.types.domain.surface import SurfaceKind


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


def record(**overrides: object) -> ScopeRecordRef:
    data: dict[str, object] = {
        "kind": SurfaceKind.MARKER_COMMENT,
        "issue_key": "EXT/42",
        "marker": "[scope-terminal:job-1]",
        "comment_key": "comment-1",
    }
    return ScopeRecordRef.model_validate(data | overrides)


def criterion_record(issue_key: str = "EXT/43") -> ScopeRecordRef:
    return ScopeRecordRef(kind=SurfaceKind.CRITERION_SUB_ISSUE, issue_key=issue_key)


def item(**overrides: object) -> ScopeResidualItem:
    data: dict[str, object] = {
        "issue_id": "EXT/42",
        "residual_class": ScopeResidualClass.UNCONVERGED_DEFECT_CLASS,
        "record": record(),
        "detail": "The organize halt left one defect class unconverged.",
        "act": "Reopen the defect class under a fresh fire.",
        "owner": ScopeResidualOwner(
            kind=ScopeResidualOwnerKind.THIS_LANE, key="lane:alpha"
        ),
    }
    return ScopeResidualItem.model_validate(data | overrides)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("UNCONVERGED_DEFECT_CLASS", "unconverged_defect_class"),
        ("UNDEMONSTRABLE_HERE", "undemonstrable_here"),
        ("OWNED_ELSEWHERE", "owned_elsewhere"),
        ("LANE_WITHOUT_OPEN_PR", "lane_without_open_pr"),
        ("LANE_UNREPORTED", "lane_unreported"),
        ("UNRECORDED_AT_TERMINAL", "unrecorded_at_terminal"),
    ],
)
def test_each_residual_class_is_present_by_name_and_value(
    name: str, value: str
) -> None:
    member = ScopeResidualClass[name]
    assert member.value == value


def test_only_an_absent_record_and_an_absent_report_block() -> None:
    assert BLOCKING_RESIDUAL_CLASSES == frozenset(
        {
            ScopeResidualClass.UNRECORDED_AT_TERMINAL,
            ScopeResidualClass.LANE_UNREPORTED,
        }
    )
    assert BLOCKING_RESIDUAL_CLASSES < frozenset(ScopeResidualClass)


def test_machine_complete_is_no_member_of_either_vocabulary() -> None:
    residual_words = {
        word
        for member in ScopeResidualClass
        for word in (member.name.lower(), member.value)
    }
    outcome_words = {
        word
        for member in WorkflowOutcome
        for word in (member.name.lower(), member.value)
    }
    for word in residual_words | outcome_words:
        assert "machine_complete" not in word


def test_a_residual_item_carries_issue_class_record_detail_act_and_owner() -> None:
    assert set(ScopeResidualItem.model_fields) == {
        "issue_id",
        "residual_class",
        "record",
        "detail",
        "act",
        "owner",
    }
    carried = item()
    assert carried.issue_id == "EXT/42"
    assert carried.residual_class is ScopeResidualClass.UNCONVERGED_DEFECT_CLASS
    assert carried.record == record()
    assert carried.detail
    assert carried.act
    assert carried.owner.kind is ScopeResidualOwnerKind.THIS_LANE


def test_the_record_reference_admits_a_criterion_sub_issue_beside_marker_keys() -> None:
    sub_issue = criterion_record()
    assert sub_issue.issue_key == "EXT/43"
    assert sub_issue.marker is None
    assert sub_issue.comment_key is None

    comment = record()
    assert comment.marker == "[scope-terminal:job-1]"
    assert comment.comment_key == "comment-1"

    unread = record(comment_key=None)
    assert unread.comment_key is None


@pytest.mark.parametrize(
    "kind",
    sorted(
        set(SurfaceKind) - {SurfaceKind.CRITERION_SUB_ISSUE, SurfaceKind.MARKER_COMMENT}
    ),
)
def test_no_other_surface_addresses_a_residual_record(kind: SurfaceKind) -> None:
    with pytest.raises(ValidationError):
        ScopeRecordRef(kind=kind, issue_key="EXT/42")


@pytest.mark.parametrize("marker", [None, " "])
def test_a_marker_comment_record_without_a_marker_is_refused(
    marker: str | None,
) -> None:
    with pytest.raises(ValidationError):
        record(marker=marker)


@pytest.mark.parametrize(
    "extra",
    [{"marker": "[scope-terminal:job-1]"}, {"comment_key": "comment-1"}],
)
def test_a_criterion_sub_issue_record_takes_no_comment_address(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ScopeRecordRef.model_validate(
            {"kind": SurfaceKind.CRITERION_SUB_ISSUE, "issue_key": "EXT/43", **extra}
        )


def test_an_unrecorded_item_is_owed_by_a_lane_and_never_by_an_operator() -> None:
    with pytest.raises(ValidationError):
        item(
            residual_class=ScopeResidualClass.UNRECORDED_AT_TERMINAL,
            owner=ScopeResidualOwner(
                kind=ScopeResidualOwnerKind.OPERATOR, key="founder"
            ),
        )
    owed_here = item(
        residual_class=ScopeResidualClass.UNRECORDED_AT_TERMINAL,
        owner=ScopeResidualOwner(
            kind=ScopeResidualOwnerKind.THIS_LANE, key="lane:alpha"
        ),
    )
    assert owed_here.owner.kind is ScopeResidualOwnerKind.THIS_LANE


@pytest.mark.parametrize(
    "residual_class",
    [ScopeResidualClass.UNDEMONSTRABLE_HERE, ScopeResidualClass.OWNED_ELSEWHERE],
)
def test_a_criterion_disposition_points_at_its_own_sub_issue(
    residual_class: ScopeResidualClass,
) -> None:
    with pytest.raises(ValidationError):
        item(residual_class=residual_class, record=record())
    recorded = item(residual_class=residual_class, record=criterion_record())
    assert recorded.record.kind is SurfaceKind.CRITERION_SUB_ISSUE


def test_one_record_carries_one_item_of_a_class() -> None:
    with pytest.raises(ValidationError):
        ScopeResidual(items=(item(), item()))
    distinct = ScopeResidual(
        items=(
            item(),
            item(residual_class=ScopeResidualClass.LANE_WITHOUT_OPEN_PR),
            item(issue_id="EXT/44"),
        )
    )
    assert len(distinct.items) == 3


def test_an_empty_residual_needs_no_items() -> None:
    empty = ScopeResidual()
    assert empty.items == ()
    assert empty.blocking == ()
    assert empty.by_class(ScopeResidualClass.OWNED_ELSEWHERE) == ()
    assert empty.by_owner(ScopeResidualOwnerKind.OPERATOR) == ()


def test_machine_complete_versus_complete_is_a_query_over_class_and_owner() -> None:
    operator = ScopeResidualOwner(kind=ScopeResidualOwnerKind.OPERATOR, key="founder")
    undemonstrable = item(
        issue_id="EXT/43",
        residual_class=ScopeResidualClass.UNDEMONSTRABLE_HERE,
        record=criterion_record(),
        owner=operator,
    )
    elsewhere = item(
        issue_id="EXT/45",
        residual_class=ScopeResidualClass.OWNED_ELSEWHERE,
        record=criterion_record("EXT/45"),
        owner=ScopeResidualOwner(
            kind=ScopeResidualOwnerKind.ANOTHER_LANE, key="lane:beta"
        ),
    )
    residual = ScopeResidual(items=(undemonstrable, elsewhere))

    assert residual.blocking == ()
    assert residual.by_class(ScopeResidualClass.UNDEMONSTRABLE_HERE) == (
        undemonstrable,
    )
    assert residual.by_owner(ScopeResidualOwnerKind.OPERATOR) == (undemonstrable,)
    assert residual.by_owner(ScopeResidualOwnerKind.THIS_LANE) == ()

    silent = item(
        issue_id="EXT/46",
        residual_class=ScopeResidualClass.LANE_UNREPORTED,
        record=record(issue_key="EXT/46"),
    )
    stopped = ScopeResidual(items=(undemonstrable, elsewhere, silent))
    assert stopped.blocking == (silent,)
    assert stopped.by_class(ScopeResidualClass.LANE_UNREPORTED) == (silent,)
