"""The accept gate's arithmetic — computed from the class, never judged.

The gate reads two things: whether a criterion passed, and which
partition the sweep left it in.  A hard gate that failed rejects the run.
A soft signal that failed cannot reject it — that is what the partition
MEANS — but it must not vanish either, so it ships with a flag.

No model call happens here and none may: the class was decided
at generation time and possibly forced by the sweep, and the pass/fail
was decided by the evaluator.  This module only counts.
"""

from collections.abc import Sequence

from kodezart.domain.errors import UngroundedVerdictError
from kodezart.types.domain.accept import AcceptVerdict, FlaggedItem, SherlockFlag
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import (
    CriterionClass,
    CriterionVerdict,
    ValidatedCriterion,
)


def is_graded(criterion: ValidatedCriterion) -> bool:
    """Whether this criterion's demonstration was possible at all.

    An ``unverifiable`` criterion is never a pass — nothing established it,
    and the sweep's own vocabulary forbids coercing that into one — and
    never a fail, because it was not refuted and the fault lies outside its
    text.  There is no third seat, so it takes none: not in the numerator,
    not in the denominator.
    """
    return criterion.feasibility.verdict is not CriterionVerdict.unverifiable


def ungraded(criteria: Sequence[ValidatedCriterion]) -> list[ValidatedCriterion]:
    """The criteria the sweep left with no possible demonstration."""
    return [criterion for criterion in criteria if not is_graded(criterion)]


def named_resource(criterion: ValidatedCriterion) -> str:
    """The resource whose absence blocks an ungraded criterion's demonstration.

    An ``unverifiable`` verdict that names nothing has established nothing,
    which is a refuter that produced no verdict rather than a verdict of
    ``unverifiable``.  It is refused here rather than rendered as an absence
    the reader is left to interpret.
    """
    resource = criterion.feasibility.missing_resource
    if resource is None or not resource.strip():
        msg = "An ungraded criterion reached the accept gate naming no resource"
        raise UngroundedVerdictError(msg, criterion_id=criterion.id)
    return resource


def _failures(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
) -> list[tuple[ValidatedCriterion, CriterionResult | None]]:
    """Every GRADED criterion that did not pass, with its result.

    Iteration is over the graded half of the dispatched set: an id with no
    result did not pass, which is the same fail-closed denominator grading
    uses, and an ungraded id is absent because it has no seat here at all.
    """
    answered = {result.criterion_id: result for result in results}
    return [
        (criterion, answered.get(criterion.id))
        for criterion in criteria
        if is_graded(criterion)
        and (criterion.id not in answered or not answered[criterion.id].passed)
    ]


def accept_verdict(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
) -> AcceptVerdict:
    """The three-state verdict for one evaluation pass.

    An ungraded criterion clamps the ceiling to ``ship_with_flags``.
    Excluding it from the arithmetic and still returning ``accepted``
    would let a run claim it satisfied a criterion nobody graded — the
    coercion into a pass, reached by arithmetic instead of by an enum.
    ``rejected`` would be the mirror fault: blocking correct work because
    the runner lacked a resource.
    """
    failures = _failures(criteria, results)
    if any(
        criterion.criterion_class is CriterionClass.hard_gate
        for criterion, _ in failures
    ):
        return AcceptVerdict.rejected
    if failures or ungraded(criteria):
        return AcceptVerdict.ship_with_flags
    return AcceptVerdict.accepted


def gate_cleared(verdict: AcceptVerdict) -> bool:
    """Whether the hard gates passed — the one routing question.

    ``accepted`` and ``ship_with_flags`` take the same route because they
    differ in what must be SAID, not in whether the work ships.  The
    verdict itself is never stored as this predicate.
    """
    return verdict is not AcceptVerdict.rejected


def sherlock_items(sherlock_flags: Sequence[SherlockFlag]) -> list[FlaggedItem]:
    """The evaluator's own concerns, as items a pull-request reader sees.

    Every evaluation pass raises these — the loop's and the post-merge
    review's alike — so the mapping lives here rather than at each call
    site that has to carry a pass's flags to the body.
    """
    return [
        FlaggedItem(criterion_id=flag.criterion_id, summary=flag.concern)
        for flag in sherlock_flags
    ]


def flagged_items(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
    sherlock_flags: Sequence[SherlockFlag],
) -> list[FlaggedItem]:
    """Everything a flagged run owes its reader, in one ordered list.

    Three producers, one list: a failing soft signal, an ungraded
    criterion, and a ``[sherlock]`` concern.  The ungraded entries carry
    the resource their ``unverifiable`` verdict named, because an id alone
    tells the reader nothing about what would settle it.
    """
    items = [
        FlaggedItem(
            criterion_id=criterion.id,
            summary=(
                f"{criterion.text} — "
                f"{result.reasoning if result is not None else 'no verdict returned'}"
            ),
        )
        for criterion, result in _failures(criteria, results)
    ]
    items.extend(
        FlaggedItem(
            criterion_id=criterion.id,
            summary=(
                f"{criterion.text} — ungraded: demonstration deferred, "
                f"{named_resource(criterion)} absent"
            ),
        )
        for criterion in ungraded(criteria)
    )
    items.extend(sherlock_items(sherlock_flags))
    return items


FLAGGED_HEADING = "## Shipped with flags"


def append_flagged_section(body: str, items: Sequence[FlaggedItem]) -> str:
    """Append the flagged items to a pull-request *body*, verbatim.

    Composed by the harness, never asked of a model: a flag the reader is
    supposed to act on cannot depend on a generator choosing to mention
    it.  An empty item list leaves the body byte-identical.
    """
    if not items:
        return body
    lines = [
        f"- {item.criterion_id}: {item.summary}"
        if item.criterion_id is not None
        else f"- {item.summary}"
        for item in items
    ]
    return "\n\n".join([body, FLAGGED_HEADING, "\n".join(lines)])
