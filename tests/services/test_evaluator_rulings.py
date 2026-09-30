"""The evaluator's ruling record moves a failed claim back, and only a claim."""

import structlog

from kodezart.services.evaluator_rulings import EvaluatorRulingWriter
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import CriterionId
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    FakeEvaluatorRulingTracker,
    FakeScopeStatusWriter,
    make_tracker_issue,
)

PARENT = "fire/parent"
#: Each criterion's board state, and whether the evaluation passed it.
BOARD = {
    "c/in-review": ("in_review", WorkflowStateKind.STARTED, False),
    "c/in-progress": (
        LifecycleStage.IN_PROGRESS.value,
        WorkflowStateKind.STARTED,
        False,
    ),
    "c/done": ("done", WorkflowStateKind.COMPLETED, False),
    "c/triage": ("Triage", WorkflowStateKind.TRIAGE, False),
    "c/todo": ("Todo", WorkflowStateKind.UNSTARTED, False),
    "c/backlog": ("Backlog", WorkflowStateKind.BACKLOG, False),
    "c/canceled": ("Canceled", WorkflowStateKind.CANCELED, False),
    "c/duplicate": ("Duplicate", WorkflowStateKind.DUPLICATE, False),
    "c/passed-done": ("done", WorkflowStateKind.COMPLETED, True),
}


def _evaluation() -> AcceptanceCriteriaOutput:
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=CriterionId(key),
                criterion=key,
                passed=passed,
                reasoning="ran the test",
            )
            for key, (_, _, passed) in BOARD.items()
        ]
    )


async def test_a_failed_claim_moves_back_from_started_or_completed_only() -> None:
    """In review and done move to in progress; in progress is read and left.

    A criterion in a triage, unstarted, backlog, canceled or duplicate state
    is not moved, nor is one the evaluation passed, and ``state_moves``
    counts only the moves that changed the state.  The board holds a failed
    criterion in every kind, so a kind added later is red here until it is
    placed on one side of the selection by a diff.
    """
    assert {kind for _, kind, passed in BOARD.values() if not passed} == set(
        WorkflowStateKind
    )
    tracker = FakeEvaluatorRulingTracker(
        issues=[
            make_tracker_issue(PARENT),
            *(
                make_tracker_issue(
                    key, state_name=name, state_kind=kind, parent_key=PARENT
                )
                for key, (name, kind, _) in BOARD.items()
            ),
        ]
    )
    writer = EvaluatorRulingWriter(tracker=tracker, status=FakeScopeStatusWriter())

    with structlog.testing.capture_logs() as logs:
        await writer.record(
            scope=ScopeRef(kind=ScopeKind.ISSUE, key=PARENT),
            iteration=1,
            previous=None,
            current=_evaluation(),
        )

    assert tracker.workflow_writes == [
        ("c/in-review", LifecycleStage.IN_PROGRESS),
        ("c/done", LifecycleStage.IN_PROGRESS),
    ]
    assert tracker.issues["c/in-progress"].state_name == (
        LifecycleStage.IN_PROGRESS.value
    )
    (recorded,) = [log for log in logs if log["event"] == "rulings_recorded"]
    assert recorded["state_moves"] == 2
