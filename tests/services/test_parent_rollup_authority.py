"""A merged fire does not grant authority to mark its tracker parent Done."""

import pytest

from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, make_tracker_issue
from tests.services.test_lifecycle_watcher import (
    ISSUE,
    JOB_ID,
    PR_EVENT,
    PRE_CLAIM_STATE,
    complete,
    watcher_over,
)


@pytest.mark.parametrize(
    "criterion_state", [WorkflowStateKind.UNSTARTED, WorkflowStateKind.COMPLETED]
)
async def test_merge_reports_delivery_without_writing_parent_or_criterion_done(
    criterion_state: WorkflowStateKind,
) -> None:
    criterion = make_tracker_issue(
        "leaf/check",
        parent_key=ISSUE,
        issue_labels=frozenset({"criterion"}),
        state_name="Done" if criterion_state is WorkflowStateKind.COMPLETED else "Todo",
        state_kind=criterion_state,
    )
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE), criterion])
    watch = watcher_over(
        tracker, PR_EVENT, complete(merged=True, outcome=WorkflowOutcome.ci_passed)
    )

    await watch.watch(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)

    assert tracker.workflow_writes == [
        (ISSUE, LifecycleStage.IN_PROGRESS),
        (ISSUE, LifecycleStage.IN_REVIEW),
    ]
    assert await tracker.read_issue(issue_key=criterion.issue_key) == criterion
    assert tracker.queue_writes == [(ISSUE, QueueState.DONE)]
    assert any(
        "reached outcome ci_passed" in comment.body for comment in tracker.comments
    )
