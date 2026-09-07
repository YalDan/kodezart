"""The permutation guard both model fan-in channels are held to.

Two channels return a set keyed by dispatched criterion ids — the
evaluator's results and the validator's verdicts — and each is correct
only when what comes back is a PERMUTATION of what went out: one entry
per dispatched id, no id nobody dispatched, no id answered twice.

Detection lives here, once, so the two channels cannot drift into two
definitions of correspondence.  What each channel does after the bounded
re-dispatch differs and belongs to the channel: the evaluator grades
fail-closed, the validator halts, because a verdict nobody derived is not
a verdict the sweep may invent.

One error type across both channels and all three shapes.  A second
exception class would let a caller handle "missing" and forget
"duplicate", which is how a guard becomes a filter.
"""

from collections.abc import Sequence

from kodezart.domain.errors import CriteriaFanInError
from kodezart.types.domain.criteria import FanInReport
from kodezart.types.domain.grading import IterationGrade

FAN_IN_MESSAGE = "Returned ids are not a permutation of the dispatched criteria"


def fan_in_breach(
    *,
    missing_ids: Sequence[str],
    duplicate_ids: Sequence[str],
    unknown_ids: Sequence[str],
) -> CriteriaFanInError | None:
    """The typed breach these correspondence holes describe, or ``None``.

    The ONE construction site for the guard's error, so every raise site
    carries all three lists and no site can report a subset.
    """
    if not missing_ids and not duplicate_ids and not unknown_ids:
        return None
    return CriteriaFanInError(
        FAN_IN_MESSAGE,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        unknown_ids=unknown_ids,
    )


def require_permutation(grade: IterationGrade) -> None:
    """Raise when a graded evaluation pass is not a permutation.

    Reads the reconciliation the grading already performed rather than
    repeating it: the grade knows which ids went unanswered, which were
    invented and which were answered twice, and it computed all three
    against the dispatched set.
    """
    breach = fan_in_breach(
        missing_ids=grade.missing_ids,
        duplicate_ids=grade.duplicate_ids,
        unknown_ids=grade.unknown_ids,
    )
    if breach is not None:
        raise breach


def fan_in_report(grade: IterationGrade, *, attempts: int) -> FanInReport:
    """The wire record of a breach the bounded re-dispatch could not clear.

    Built from the grade rather than from the error so the denominator on
    the event is the same dispatched count the grading used.
    """
    return FanInReport(
        missing_ids=list(grade.missing_ids),
        unknown_ids=list(grade.unknown_ids),
        duplicate_ids=list(grade.duplicate_ids),
        dispatched_count=grade.dispatched_count,
        attempts=attempts,
    )
