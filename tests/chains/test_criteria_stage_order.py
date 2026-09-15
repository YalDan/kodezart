"""Criterion sub-issues land before the criteria-stage marker admits a fire."""

# The recorded call log of the native board is the evidence: a marker that
# outruns its own sub-issues would admit a fire with nothing to check.
import pytest

from kodezart.domain.errors import EmptyFireCriteriaError
from tests.chains.test_organize_owner import factory, run_owner
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over

CRITERIA_STAGE_LABEL_KEY = "criteria"


def labels():
    operation = declared_operation()
    return operation.issue_labels, operation.scope_labels


def fire_tracker(board):
    issue_labels, scope_labels = labels()
    return tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels=issue_labels,
        scope_labels=scope_labels,
        criteria_stage_label_key=CRITERIA_STAGE_LABEL_KEY,
    )


def children_of(board):
    return [
        key
        for key, issue in board.server.issues.items()
        if issue.parent_id == CLAIMED_ISSUE
    ]


def creations(board, *, criterion_label):
    return [
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_issue"
        and args.get("parentId") == CLAIMED_ISSUE
        and criterion_label in list(args.get("labels", []))
    ]


def marker_writes(board, *, marker):
    return [
        index
        for index, (name, args) in enumerate(board.calls)
        if name == "save_issue"
        and args.get("id") == CLAIMED_ISSUE
        and marker in list(args.get("addLabels", []))
    ]


async def test_every_criterion_sub_issue_is_created_before_the_stage_marker():
    issue_labels, _ = labels()
    owner, board, _ = factory()
    report = await run_owner(owner)
    assert report.halt is None
    minted = creations(board, criterion_label=issue_labels["criterion"])
    marked = marker_writes(board, marker=issue_labels[CRITERIA_STAGE_LABEL_KEY])
    assert len(minted) == len(children_of(board))
    assert minted and marked
    assert max(minted) < min(marked)


async def test_each_created_criterion_sub_issue_resolves_by_its_own_key():
    _, scope_labels = labels()
    owner, board, _ = factory()
    await run_owner(owner)
    tracker = fire_tracker(board)
    keys = children_of(board)
    assert keys
    for key in keys:
        resolved = await tracker.read_issue(issue_key=key)
        assert resolved.issue_key == key
    board.server.issues[CLAIMED_ISSUE].labels.append(scope_labels["approved"])
    spec = await tracker.read_fire_spec(issue_key=CLAIMED_ISSUE)
    assert sorted(spec.criteria) == sorted(keys)


async def test_stage_marker_without_criterion_sub_issues_refuses_admission():
    issue_labels, scope_labels = labels()
    owner, board, _ = factory()
    await run_owner(owner)
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.labels.append(scope_labels["approved"])
    for key in children_of(board):
        del board.server.issues[key]
    assert issue_labels[CRITERIA_STAGE_LABEL_KEY] in parent.labels
    with pytest.raises(EmptyFireCriteriaError) as caught:
        await fire_tracker(board).read_fire_spec(issue_key=CLAIMED_ISSUE)
    assert caught.value.issue_key == CLAIMED_ISSUE
