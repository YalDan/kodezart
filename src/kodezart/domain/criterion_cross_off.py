"""One cross-off per graded criterion, as arithmetic over the grade.

The evaluation session is the judgement; this module is what that verdict
becomes. It makes no judgement of its own: the state is the result's own
pass, the sha is the commit the workspace was graded at, and the Evidence
pointer names the session and the iteration that produced the verdict
rather than repeating the evaluator's prose, which nothing reads back.
"""

from collections.abc import Collection, Mapping, Sequence

from kodezart.domain.errors import StaleWriteError
from kodezart.domain.fire_spec import (
    criterion_field_bodies,
    criterion_ref,
    duplicated_row_labels,
)
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    CrossOffState,
    UndemonstratedReason,
)
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

#: The state a criterion a fire finished sits in until something takes it back.
#:
#: The fire's own evaluation step is what moved it, so such a criterion is
#: still inside the obligation the fire took on: reading it as gone would
#: make finishing work indistinguishable from the board cancelling it. The
#: barrier that keeps it in the fire's current set and the writer that takes
#: it back are two readings of this one state, so it is stated once.
HELD_CRITERION_STATE = WorkflowStateKind.COMPLETED

#: The states a criterion sub-issue can be ticked from.
#:
#: Unstarted is the ordinary first tick. Completed is a re-grade of a
#: criterion an earlier iteration of this same run already ticked, which
#: restamps its Evidence at the new head. Any other state means the board
#: took the criterion somewhere this verdict does not address.
TICKABLE_STATES = frozenset({WorkflowStateKind.UNSTARTED, HELD_CRITERION_STATE})


def undemonstrated_reasons(
    *,
    results: Sequence[CriterionResult],
    workspace_stood: bool,
    surviving_checks: Collection[CriterionId],
) -> dict[CriterionId, UndemonstratedReason]:
    """Which of this attempt's readings proved nothing, and which one failed.

    The workspace reading is about the whole tree, so when it fails nothing
    read in that tree stands and every criterion carries it — the mutation
    reading included, which is taken in a copy of that same tree. Otherwise
    the readings are one criterion at a time: a check that passed with the
    behaviour it names gone read nothing about that behaviour.
    """
    if not workspace_stood:
        return {
            result.criterion_id: UndemonstratedReason.workspace_not_the_graded_sha
            for result in results
        }
    return {
        result.criterion_id: UndemonstratedReason.check_survived_mutation
        for result in results
        if result.criterion_id in surviving_checks
    }


def evaluation_observation(*, session_id: str, iteration: int) -> str:
    """The pointer an Evidence row carries back to the grading it came from."""
    return f"evaluator session {session_id}, iteration {iteration}"


def tick_anchor(criterion: TrackerCriterion) -> str:
    """What a tick asserts about the sub-issue it addresses, as one line."""
    return (
        f"the unstarted or completed criterion {criterion.id} carrying the "
        f"Check {criterion.text!r}, at most one Evidence row and no template "
        f"field written twice"
    )


def require_tickable(*, issue: TrackerIssue, criterion: TrackerCriterion) -> None:
    """Refuse a sub-issue the verdict in hand no longer addresses.

    Read from the sub-issue as it stands rather than from what the
    evaluator remembered, so a criterion reclassified, moved or amended
    between the grading and this write takes no part of it. The refusal is
    the description surface's own stale-write error, because the tick's
    first act is a compare-and-set on that body.

    Every condition is one the write itself depends on, the body's rows
    among them: the Evidence row is set field-scoped, and a body carrying a
    template field twice names no single row for that edit to set, so it is
    the codec's own rule that is asked here rather than one row of it. A
    body with no Evidence row is tickable — the edit appends the row it
    finds absent.

    What this cannot see: a criterion someone moved INTO the finished state
    during the fire reads exactly like one the fire itself finished, because
    the roster carries no state and the Evidence row carries no run. Such a
    criterion is re-graded and, on a fail, taken back. Nothing but the
    evaluation step moves a criterion to that state, so this is a protocol
    violation on the board rather than a case to decide here.
    """
    if (
        "criterion" not in issue.issue_labels
        or issue.state_kind not in TICKABLE_STATES
        or criterion_field_bodies(issue.body, field="Check") != (criterion.text,)
        or duplicated_row_labels(issue.body)
    ):
        raise StaleWriteError(target=criterion.id, expected=tick_anchor(criterion))


def cross_off_state(
    *, passed: bool, reason: UndemonstratedReason | None
) -> CrossOffState:
    """What one result is worth, given which reading of its tree failed.

    *reason* decides before the result does: a verdict produced where no
    reading of the tree says anything about this criterion is neither its
    pass nor its fail, and recording it as either would put a claim about
    the branch on the board that nothing on the branch supports.  The
    function names the thing it decides on, because the state now has more
    than one trigger.
    """
    if reason is not None:
        return CrossOffState.undemonstrated
    return CrossOffState.passed if passed else CrossOffState.failed


def cross_offs_for(
    *,
    results: Sequence[CriterionResult],
    graded_sha: str,
    observation: str,
    reasons: Mapping[CriterionId, UndemonstratedReason],
) -> tuple[CriterionCrossOff, ...]:
    """The only site in the source that builds a cross-off and its evidence.

    One evidence value serves the whole attempt: the sha and the session
    pointer are the attempt's, not each criterion's, so a second copy per
    criterion would be a second place for the same sha to drift from.
    *reasons* is per criterion, because a reading can fail for one
    criterion of an attempt and hold for the next.
    """
    evidence = CriterionEvidence(graded_sha=graded_sha, test=observation)
    return tuple(
        CriterionCrossOff(
            criterion=criterion_ref(result.criterion_id),
            state=cross_off_state(
                passed=result.passed, reason=reasons.get(result.criterion_id)
            ),
            evidence=evidence,
            undemonstrated_reason=reasons.get(result.criterion_id),
        )
        for result in results
    )
