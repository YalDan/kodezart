"""Grade one evaluation pass against the DISPATCHED criteria set.

The evaluator returns results; the harness decides what they are worth.
Reconciliation is keyed by ``criterion_id`` and the denominator is the
dispatched count, so:

* a result for an id nobody dispatched is discarded and named;
* an id answered TWICE grades FAILED and is named: two answers are the
  model contradicting itself, so the criterion has no verdict, and
  letting the first answer stand was this module's one optimistic arm;
* an id with no result grades FAILED and is named — never a silently
  shorter denominator, and never acceptance over a partial set;
* the text carried forward — into the report AND into the next
  iteration's feedback — is the harness's own, looked up by id, so an
  echoed whitespace or backslash mutation changes neither the keying nor
  the criterion anybody downstream reads.

The missing / unknown / duplicate DETECTION above is what the permutation
guard reads: :func:`kodezart.domain.fan_in.require_permutation` turns it
into the retryable error, and the node re-dispatches within its bound
before this grading is allowed to stand.  Both fan-in channels answer to
that one definition of correspondence — the sweep's ``reconcile`` raises
the same error from the same construction site.
Measured, not feared: ``criteria_results`` is ``Field(min_length=1)`` and
acceptance was once ``all(...)`` over whatever returned, so three results
for ten criteria yielded acceptance over the partial set.

The guard does NOT retire this arm; it sits in front of it.  A model that
answers the same wrong shape every time exhausts the bound, and what runs
then is exactly what runs here: the dispatched denominator, a missing id
graded failed and named, and the holes reported on the emitted event so
the fail-closed verdict is legible as one rather than as a quiet loss.

A criterion the harness read nothing about is WITHHELD here and nowhere
else: it grades failed carrying the reading that failed, after every arm
above has spoken, so withholding is the last word on a verdict and says
nothing at all about the roster.

``results`` carries a row for every dispatched id.  The ARITHMETIC is
narrower: an ``unverifiable`` criterion seats in neither ``passed_count``
nor ``failures``, which is what keeps a criterion nothing can grade out of
the iteration feedback instead of recurring every round.  Its presence
clamps the verdict to ``ship_with_flags``.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from kodezart.domain.accept_gate import accept_verdict, is_graded
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import (
    CriterionFailure,
    CriterionId,
    ExecutionCriterion,
)
from kodezart.types.domain.criterion_lifecycle import (
    RederivationClass,
    UndemonstratedReason,
)
from kodezart.types.domain.grading import IterationGrade

MISSING_RESULT_REASONING = (
    "The evaluator returned no result for this criterion id. "
    "A dispatched criterion with no verdict grades failed."
)

DUPLICATE_RESULT_REASONING = (
    "The evaluator returned more than one result for this criterion id. "
    "A criterion answered twice has no verdict, so it grades failed."
)

#: What stands in for a verdict when the grading proved nothing, one
#: sentence per reading.
#:
#: Fixed text rather than the evaluator's own words, for the same reason the
#: two sentences above are: the evaluator answered about a tree, and what
#: these say is what the harness read about the tree it answered in. That is
#: the harness's reading, not the session's, so the session's prose would
#: misattribute it.
UNDEMONSTRATED_REASONS: Final[Mapping[UndemonstratedReason, str]] = MappingProxyType(
    {
        UndemonstratedReason.workspace_not_the_graded_sha: (
            "undemonstrated: the grading workspace held uncommitted changes, or its "
            "head was not the sha this verdict would be stamped with, so what it read "
            "is not what that sha names"
        ),
    }
)

#: Nothing this attempt read was withheld from any criterion.
NO_WITHDRAWALS: Final[Mapping[CriterionId, UndemonstratedReason]] = MappingProxyType({})


def grade_iteration(
    criteria: Sequence[ExecutionCriterion],
    output: AcceptanceCriteriaOutput,
    *,
    undemonstrated: Mapping[CriterionId, UndemonstratedReason] = NO_WITHDRAWALS,
) -> IterationGrade:
    """Reconcile *output* against *criteria* and grade fail-closed.

    A criterion the harness read nothing about grades failed with the
    reading that failed in place of the evaluator's words, whatever it
    answered — the last word on a verdict, and the only thing
    *undemonstrated* changes.  What corresponds to what is untouched by it:
    an id nobody answered is still named missing and an id answered twice
    still named duplicate, because those facts are about the roster and not
    about the tree.
    """
    dispatched = {criterion.id: criterion for criterion in criteria}
    answered: dict[CriterionId, CriterionResult] = {}
    unknown_ids: list[CriterionId] = []
    duplicate_ids: list[CriterionId] = []

    for result in output.criteria_results:
        if result.criterion_id not in dispatched:
            unknown_ids.append(result.criterion_id)
            continue
        if result.criterion_id in answered:
            duplicate_ids.append(result.criterion_id)
            continue
        answered[result.criterion_id] = result

    results: list[CriterionResult] = []
    failures: list[CriterionFailure] = []
    missing_ids: list[CriterionId] = []
    duplicated = set(duplicate_ids)

    for criterion in criteria:
        answer = answered.get(criterion.id)
        # The declaration travels with the verdict: this rebuild is the only
        # thing between the answer and the cross-off, so a class dropped here
        # is a class no criterion ever holds. A criterion with no answer, or
        # two, declares nothing, exactly as it passes nothing.
        declared = RederivationClass.cheap
        exercised: tuple[str, ...] = ()
        if answer is None:
            missing_ids.append(criterion.id)
            passed, reasoning = False, MISSING_RESULT_REASONING
        elif criterion.id in duplicated:
            passed, reasoning = False, DUPLICATE_RESULT_REASONING
        else:
            passed, reasoning = answer.passed, answer.reasoning
            declared, exercised = answer.rederivation_class, answer.exercised_paths
        withheld = undemonstrated.get(criterion.id)
        if withheld is not None:
            passed, reasoning = False, UNDEMONSTRATED_REASONS[withheld]
        # The report carries the harness's text, never the echo.
        results.append(
            CriterionResult(
                criterion_id=criterion.id,
                criterion=criterion.text,
                passed=passed,
                reasoning=reasoning,
                rederivation_class=declared,
                exercised_paths=exercised,
            )
        )
        if not passed:
            failures.append(
                CriterionFailure(
                    criterion_id=criterion.id,
                    text=criterion.text,
                    reasoning=reasoning,
                )
            )

    graded_ids = {criterion.id for criterion in criteria if is_graded(criterion)}
    failures = [failure for failure in failures if failure.criterion_id in graded_ids]
    passed_count = sum(
        1 for result in results if result.passed and result.criterion_id in graded_ids
    )
    return IterationGrade(
        results=results,
        failures=failures,
        missing_ids=missing_ids,
        unknown_ids=unknown_ids,
        duplicate_ids=duplicate_ids,
        dispatched_count=len(criteria),
        passed_count=passed_count,
        verdict=accept_verdict(criteria, results),
        sherlock_flags=list(output.sherlock_flags),
    )
