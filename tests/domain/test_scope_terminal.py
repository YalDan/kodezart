"""Retired terminal wrappers do not remove already public outcome values."""

import json
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from kodezart.config.app import AppConfig
from kodezart.domain.scope_terminal import BOUND_CONFIG_FIELD, stopping_rule_of
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.organize_owner import OrganizeBoundEvidence
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_terminal import (
    BLOCKING_RESIDUAL_CLASSES,
    SCOPE_TERMINAL_OUTCOMES,
    LaneReportState,
    ScopeLaneEntry,
    ScopeRecordRef,
    ScopeResidual,
    ScopeResidualClass,
    ScopeResidualItem,
    ScopeResidualOwner,
    ScopeResidualOwnerKind,
    ScopeStoppingRule,
    ScopeTerminalEvent,
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


def bound(**overrides: object) -> OrganizeBoundEvidence:
    data: dict[str, object] = {
        "setting": "organize.max_admission_rounds",
        "value": 3,
        "rounds_used": 3,
        "loop": "admission",
    }
    return OrganizeBoundEvidence.model_validate(data | overrides)


@pytest.mark.parametrize(
    "config_field",
    [
        "organize.max_admission_rounds",
        "MAX_ADMISSION_ROUNDS",
        "KODEZART_organize__max_admission_rounds",
        "KODEZART__ORGANIZE",
        "KODEZART_ORGANIZE__MAX ADMISSION ROUNDS",
    ],
)
def test_a_stop_reached_by_no_configured_env_field_is_no_declared_stop(
    config_field: str,
) -> None:
    with pytest.raises(ValidationError):
        ScopeStoppingRule(config_field=config_field, configured_value=3, rounds_used=3)


@pytest.mark.parametrize(("configured_value", "rounds_used"), [(3, 2), (3, 4)])
def test_a_stop_short_of_or_past_its_bound_is_refused(
    configured_value: int, rounds_used: int
) -> None:
    with pytest.raises(ValidationError):
        ScopeStoppingRule(
            config_field="KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
            configured_value=configured_value,
            rounds_used=rounds_used,
        )


@pytest.mark.parametrize(
    ("setting", "loop", "config_field"),
    [
        (
            "organize.max_admission_rounds",
            "admission",
            "KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
        ),
        (
            "organize.max_convergence_rounds",
            "convergence",
            "KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS",
        ),
        (
            "write_back.max_verify_rounds",
            "write_back",
            "KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS",
        ),
    ],
)
def test_each_bound_names_its_own_configured_env_field_value_and_rounds(
    setting: str, loop: str, config_field: str
) -> None:
    rule = stopping_rule_of(bound(setting=setting, loop=loop, value=5, rounds_used=5))

    assert rule is not None
    assert rule.config_field == config_field
    assert rule.configured_value == 5
    assert rule.rounds_used == 5


def test_a_halt_reached_by_no_bound_declares_no_stopping_rule() -> None:
    assert stopping_rule_of(None) is None


def test_every_bound_a_halt_can_name_has_a_configured_env_field() -> None:
    settings = get_args(OrganizeBoundEvidence.model_fields["setting"].annotation)

    assert set(settings)
    assert set(BOUND_CONFIG_FIELD) == set(settings)


def test_each_configured_env_field_addresses_a_declared_setting() -> None:
    prefix = AppConfig.model_config["env_prefix"]
    delimiter = AppConfig.model_config["env_nested_delimiter"]
    assert prefix is not None and delimiter is not None

    for setting, config_field in BOUND_CONFIG_FIELD.items():
        section, _, field = setting.partition(".")
        annotation = AppConfig.model_fields[section].annotation
        nested = next(
            arm
            for arm in get_args(annotation)
            if isinstance(arm, type) and issubclass(arm, BaseModel)
        )
        assert field in nested.model_fields
        assert config_field == f"{prefix}{section}{delimiter}{field}".upper()


def lane(**overrides: object) -> ScopeLaneEntry:
    data: dict[str, object] = {
        "lane_key": "lane:alpha",
        "issue_id": "EXT/42",
        "report_state": LaneReportState.CONVERGED,
        "outcome": WorkflowOutcome.ci_passed,
        "pr": LanePR(url="https://example.invalid/pr/1", number=1, state="open"),
        "branch": "kodezart/ext-42",
        "checks": CIStatus.passed,
    }
    return ScopeLaneEntry.model_validate(data | overrides)


def silent_lane(**overrides: object) -> ScopeLaneEntry:
    return lane(
        report_state=LaneReportState.UNREPORTED,
        outcome=None,
        pr=None,
        branch=None,
        checks=CIStatus.not_monitored,
        **overrides,
    )


def terminal(**overrides: object) -> ScopeTerminalEvent:
    data: dict[str, object] = {
        "scope": ScopeRef(kind=ScopeKind.PROJECT, key="project-address"),
        "lanes": (lane(),),
        "residual": ScopeResidual(),
        "outcome": WorkflowOutcome.scope_converged,
    }
    return ScopeTerminalEvent.model_validate(data | overrides)


def test_a_converged_scope_owing_work_is_refused() -> None:
    with pytest.raises(ValidationError):
        terminal(residual=ScopeResidual(items=(item(),)))

    converged = terminal()
    assert converged.outcome is WorkflowOutcome.scope_converged
    assert converged.residual.items == ()
    assert converged.stopping_rule is None
    assert converged.resumed_without_terminal is False


def test_an_unconverged_defect_class_without_a_declared_stop_is_refused() -> None:
    with pytest.raises(ValidationError):
        terminal(
            outcome=WorkflowOutcome.scope_converged_with_residual,
            residual=ScopeResidual(items=(item(),)),
        )

    declared = terminal(
        outcome=WorkflowOutcome.scope_converged_with_residual,
        residual=ScopeResidual(items=(item(),)),
        stopping_rule=ScopeStoppingRule(
            config_field="KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
            configured_value=3,
            rounds_used=3,
        ),
    )
    assert declared.stopping_rule is not None
    assert declared.stopping_rule.rounds_used == 3


def test_recorded_residual_work_alone_needs_no_declared_stop() -> None:
    recorded = terminal(
        outcome=WorkflowOutcome.scope_converged_with_residual,
        residual=ScopeResidual(
            items=(
                item(residual_class=ScopeResidualClass.LANE_WITHOUT_OPEN_PR),
                item(
                    issue_id="EXT/44",
                    residual_class=ScopeResidualClass.LANE_WITHOUT_OPEN_PR,
                    record=record(issue_key="EXT/44"),
                ),
            )
        ),
    )

    assert recorded.stopping_rule is None
    assert len(recorded.residual.by_class(ScopeResidualClass.LANE_WITHOUT_OPEN_PR)) == 2


def test_a_scope_stopped_short_carrying_a_declared_stop_is_refused() -> None:
    rule = ScopeStoppingRule(
        config_field="KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
        configured_value=3,
        rounds_used=3,
    )
    with pytest.raises(ValidationError):
        terminal(outcome=WorkflowOutcome.scope_stopped_short, stopping_rule=rule)

    stopped = terminal(
        outcome=WorkflowOutcome.scope_stopped_short,
        lanes=(silent_lane(),),
        residual=ScopeResidual(
            items=(item(residual_class=ScopeResidualClass.LANE_UNREPORTED),)
        ),
    )
    assert stopped.stopping_rule is None


@pytest.mark.parametrize(
    "outcome",
    sorted(set(WorkflowOutcome) - set(SCOPE_TERMINAL_OUTCOMES)),
)
def test_no_fire_outcome_classifies_a_scope(outcome: WorkflowOutcome) -> None:
    with pytest.raises(ValidationError):
        terminal(outcome=outcome)


def test_a_silent_lane_or_an_unread_record_forbids_convergence() -> None:
    with pytest.raises(ValidationError):
        terminal(lanes=(silent_lane(),))
    with pytest.raises(ValidationError):
        terminal(
            residual=ScopeResidual(
                items=(item(residual_class=ScopeResidualClass.UNRECORDED_AT_TERMINAL),)
            ),
        )

    unread = terminal(
        outcome=WorkflowOutcome.scope_stopped_short,
        lanes=(silent_lane(),),
        residual=ScopeResidual(
            items=(item(residual_class=ScopeResidualClass.UNRECORDED_AT_TERMINAL),)
        ),
    )
    assert unread.residual.blocking


def test_one_lane_key_and_one_issue_appear_once_in_the_vector() -> None:
    with pytest.raises(ValidationError):
        terminal(lanes=(lane(), lane(issue_id="EXT/44")))
    with pytest.raises(ValidationError):
        terminal(lanes=(lane(), lane(lane_key="lane:beta")))

    vector = terminal(
        lanes=(
            lane(),
            lane(lane_key="lane:beta", issue_id="EXT/44"),
        )
    )
    assert len(vector.lanes) == 2


def test_a_lane_is_unreported_exactly_when_it_carries_no_outcome() -> None:
    with pytest.raises(ValidationError):
        lane(report_state=LaneReportState.UNREPORTED)
    with pytest.raises(ValidationError):
        lane(report_state=LaneReportState.IN_GAP, outcome=None, pr=None, branch=None)

    silent = silent_lane()
    assert silent.outcome is None
    assert silent.pr is None
    assert silent.branch is None
    assert silent.checks is CIStatus.not_monitored


@pytest.mark.parametrize("absent", [{"branch": None}, {"pr": None}])
def test_a_converged_lane_shows_its_branch_and_pull_request(
    absent: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        lane(**absent)

    halted = lane(
        report_state=LaneReportState.HALTED,
        outcome=WorkflowOutcome.loop_plateaued,
        pr=None,
        branch=None,
        checks=CIStatus.not_monitored,
    )
    assert halted.pr is None


def test_a_terminal_lane_entry_records_no_merge() -> None:
    assert "merge" not in " ".join(ScopeLaneEntry.model_fields)


def test_a_converged_scope_states_its_empty_residual_and_absent_stop_on_the_wire() -> (
    None
):
    converged = terminal()

    encoded = json.loads(converged.model_dump_json(by_alias=True))
    assert "residual" in encoded
    assert encoded["residual"]["items"] == []
    assert "stoppingRule" in encoded
    assert encoded["stoppingRule"] is None

    dumped = converged.model_dump(by_alias=True, exclude_none=False)
    assert dumped["residual"]["items"] == ()
    assert "stoppingRule" in dumped
    assert dumped["stoppingRule"] is None
