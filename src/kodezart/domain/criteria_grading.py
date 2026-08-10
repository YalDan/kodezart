"""Grade one evaluation pass against the DISPATCHED criteria set.

The evaluator returns results; the harness decides what they are worth.
Reconciliation is keyed by ``criterion_id`` and the denominator is the
dispatched count, so:

* a result for an id nobody dispatched is discarded and named;
* a second result for an id already answered is discarded and named;
* an id with no result grades FAILED and is named — never a silently
  shorter denominator, and never acceptance over a partial set;
* the text carried forward — into the report AND into the next
  iteration's feedback — is the harness's own, looked up by id, so an
  echoed whitespace or backslash mutation changes neither the keying nor
  the criterion anybody downstream reads.

The missing / unknown / duplicate DETECTION above is a KOD-91 workaround
rather than architecture.  Server-side strict enforcement does not engage
for any schema kodezart ships — every one uses keywords outside the strict
allowlist — so nothing upstream rejects a partial or padded result set.
Measured, not feared: ``criteria_results`` is ``Field(min_length=1)`` and
acceptance is ``all(...)`` over whatever returned, so three results for ten
criteria yielded acceptance over the partial set.

KOD-91 is the expiry, and its deliverable 4 covers BOTH fan-in channels in
terms — "The identical guard applies to the KOD-66 validator's verdict
set: one verdict per dispatched id".  What KOD-91 retires is the detection
half, by retrying a conforming result set into existence.  What survives it
is the fail-closed GRADING arm, because deliverable 4 "falls through to
KOD-11's fail-closed grading" once the retries are exhausted.  The
detection half is retained TODAY on a scope boundary — KOD-11 assigns the
retrying guard to KOD-91 — and not on a mechanism gap.

``results`` carries a row for every dispatched id, because that is the
report.  The ARITHMETIC is narrower: an ``unverifiable`` criterion is
neither a pass nor a fail, so it seats in neither ``passed_count`` nor
``failures`` — the second half is what keeps it out of the iteration
feedback, where a criterion nothing can grade would otherwise recur every
round and burn the budget.  Its presence clamps the verdict to
``ship_with_flags``.
"""

from collections.abc import Sequence

from kodezart.domain.accept_gate import accept_verdict, is_graded
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import (
    CriterionFailure,
    CriterionId,
    ValidatedCriterion,
)
from kodezart.types.domain.grading import IterationGrade

MISSING_RESULT_REASONING = (
    "The evaluator returned no result for this criterion id. "
    "A dispatched criterion with no verdict grades failed."
)


def grade_iteration(
    criteria: Sequence[ValidatedCriterion],
    output: AcceptanceCriteriaOutput,
) -> IterationGrade:
    """Reconcile *output* against *criteria* and grade fail-closed."""
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

    for criterion in criteria:
        answer = answered.get(criterion.id)
        if answer is None:
            missing_ids.append(criterion.id)
            results.append(
                CriterionResult(
                    criterion_id=criterion.id,
                    criterion=criterion.text,
                    passed=False,
                    reasoning=MISSING_RESULT_REASONING,
                )
            )
            failures.append(
                CriterionFailure(
                    criterion_id=criterion.id,
                    text=criterion.text,
                    reasoning=MISSING_RESULT_REASONING,
                )
            )
            continue
        # The REPORT carries the harness's text, not the echo: the oracle is
        # byte-identical across the criteria event, the persisted artifact and
        # every iteration's evaluation, with identity carried by the id.
        results.append(
            CriterionResult(
                criterion_id=criterion.id,
                criterion=criterion.text,
                passed=answer.passed,
                reasoning=answer.reasoning,
            )
        )
        if not answer.passed:
            failures.append(
                CriterionFailure(
                    criterion_id=criterion.id,
                    text=criterion.text,
                    reasoning=answer.reasoning,
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
