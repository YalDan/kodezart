"""The accept gate's arithmetic — computed from classification, never judged.

The gate reads two things: whether a criterion passed, and which
partition the sweep left it in.  A hard gate that failed rejects the run.
A soft signal that failed cannot reject it — that is what the partition
MEANS — but it must not vanish either, so it ships with a flag.

No model call happens here and none may: the classification was decided
at generation time and possibly forced by the sweep, and the pass/fail
was decided by the evaluator.  This module only counts.
"""

from collections.abc import Sequence

from kodezart.types.domain.accept import AcceptVerdict, FlaggedItem, SherlockFlag
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionClassification, ValidatedCriterion


def _failures(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
) -> list[tuple[ValidatedCriterion, CriterionResult | None]]:
    """Every dispatched criterion that did not pass, with its result.

    Iteration is over the DISPATCHED set: an id with no result did not
    pass, which is the same fail-closed denominator grading uses.
    """
    answered = {result.criterion_id: result for result in results}
    return [
        (criterion, answered.get(criterion.id))
        for criterion in criteria
        if criterion.id not in answered or not answered[criterion.id].passed
    ]


def accept_verdict(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
) -> AcceptVerdict:
    """The three-state verdict for one evaluation pass."""
    failures = _failures(criteria, results)
    if any(
        criterion.classification is CriterionClassification.hard_gate
        for criterion, _ in failures
    ):
        return AcceptVerdict.rejected
    if failures:
        return AcceptVerdict.ship_with_flags
    return AcceptVerdict.accepted


def gate_cleared(verdict: AcceptVerdict) -> bool:
    """Whether the hard gates passed — the one routing question.

    ``accepted`` and ``ship_with_flags`` take the same route because they
    differ in what must be SAID, not in whether the work ships.  The
    verdict itself is never stored as this predicate.
    """
    return verdict is not AcceptVerdict.rejected


def flagged_items(
    criteria: Sequence[ValidatedCriterion],
    results: Sequence[CriterionResult],
    sherlock_flags: Sequence[SherlockFlag],
) -> list[FlaggedItem]:
    """Everything a flagged run owes its reader, in one ordered list."""
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
        FlaggedItem(criterion_id=flag.criterion_id, summary=flag.concern)
        for flag in sherlock_flags
    )
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
