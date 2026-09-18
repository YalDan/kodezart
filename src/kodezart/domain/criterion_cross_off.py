"""One cross-off per graded criterion, as arithmetic over the grade.

The evaluation session is the judgement; this module is what that verdict
becomes. It makes no judgement of its own: the state is the result's own
pass, the sha is the commit the workspace was graded at, and the Evidence
pointer names the session and the iteration that produced the verdict
rather than repeating the evaluator's prose, which nothing reads back.
"""

from collections.abc import Sequence

from kodezart.domain.errors import StaleWriteError
from kodezart.domain.fire_spec import criterion_field_bodies, criterion_ref
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

#: The states a criterion sub-issue can be ticked from.
#:
#: Unstarted is the ordinary first tick. Completed is a re-grade of a
#: criterion an earlier iteration of this same run already ticked, which
#: restamps its Evidence at the new head. Any other state means the board
#: took the criterion somewhere this verdict does not address.
TICKABLE_STATES = frozenset({WorkflowStateKind.UNSTARTED, WorkflowStateKind.COMPLETED})


def evaluation_observation(*, session_id: str, iteration: int) -> str:
    """The pointer an Evidence row carries back to the grading it came from."""
    return f"evaluator session {session_id}, iteration {iteration}"


def tick_anchor(criterion: TrackerCriterion) -> str:
    """What a tick asserts about the sub-issue it addresses, as one line."""
    return (
        f"the unstarted or completed criterion {criterion.id} "
        f"carrying the Check {criterion.text!r}"
    )


def require_tickable(*, issue: TrackerIssue, criterion: TrackerCriterion) -> None:
    """Refuse a sub-issue the verdict in hand no longer addresses.

    Read from the sub-issue as it stands rather than from what the
    evaluator remembered, so a criterion reclassified, moved or amended
    between the grading and this write takes no part of it. The refusal is
    the description surface's own stale-write error, because the tick's
    first act is a compare-and-set on that body.
    """
    if (
        "criterion" not in issue.issue_labels
        or issue.state_kind not in TICKABLE_STATES
        or criterion_field_bodies(issue.body, field="Check") != (criterion.text,)
    ):
        raise StaleWriteError(target=criterion.id, expected=tick_anchor(criterion))


def cross_offs_for(
    *,
    results: Sequence[CriterionResult],
    graded_sha: str,
    observation: str,
) -> tuple[CriterionCrossOff, ...]:
    """The only site in the source that builds a cross-off and its evidence.

    One evidence value serves the whole attempt: the sha and the session
    pointer are the attempt's, not each criterion's, so a second copy per
    criterion would be a second place for the same sha to drift from.
    """
    evidence = CriterionEvidence(graded_sha=graded_sha, test=observation)
    return tuple(
        CriterionCrossOff(
            criterion=criterion_ref(result.criterion_id),
            state=CrossOffState.passed if result.passed else CrossOffState.failed,
            evidence=evidence,
        )
        for result in results
    )
