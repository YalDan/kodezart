"""Grading is keyed by id and reconciled against the dispatched set.

KOD-53/AC-19 (a partial return grades fail-closed over the dispatched
denominator) and KOD-53/AC-20 (an echoed-text mutation changes neither the
keying nor the text carried forward) are demonstrated here.
"""

from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import sweep
from kodezart.domain.criteria_grading import (
    DUPLICATE_RESULT_REASONING,
    MISSING_RESULT_REASONING,
    grade_iteration,
)
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import (
    CostClaim,
    CostMeasurement,
    CriteriaValidationOutput,
    CriterionFeasibility,
    CriterionFinding,
    CriterionVerdict,
    DraftedCriterion,
    RepairKind,
    ValidatedCriterion,
)
from tests.fakes import as_validated


def _criteria(count: int) -> list[ValidatedCriterion]:
    """The dispatch shape: minted, then carrying the sweep's verdict.

    Grading is over criteria that reached the loop, and nothing reaches the
    loop before the sweep — so the fixture supplies the verdict the loop
    would actually be holding rather than a pre-sweep criterion.
    """
    return as_validated(
        mint_criteria(
            [
                DraftedCriterion(text=f"Criterion number {n}")
                for n in range(1, count + 1)
            ]
        )
    )


def test_partial_return_grades_the_missing_ids_failed_and_keeps_the_denominator() -> (
    None
):
    """KOD-53/AC-19 — 3 of 10 ids answered: 7 fail, denominator stays 10."""
    criteria = _criteria(10)
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=criterion.id,
                criterion=criterion.text,
                passed=True,
                reasoning="verified",
            )
            for criterion in criteria[:3]
        ],
    )

    grade = grade_iteration(criteria, output)

    assert grade.dispatched_count == 10
    assert len(grade.results) == 10
    assert grade.passed_count == 3
    assert grade.verdict is AcceptVerdict.rejected
    assert grade.missing_ids == [f"AC-{n}" for n in range(4, 11)]
    assert [r.passed for r in grade.results] == [True] * 3 + [False] * 7
    assert all(
        r.reasoning == MISSING_RESULT_REASONING
        for r in grade.results
        if r.criterion_id in grade.missing_ids
    )
    assert [f.criterion_id for f in grade.failures] == grade.missing_ids


def test_a_full_pass_over_a_partial_return_is_not_acceptance() -> None:
    """Every returned result passing does not accept a partial set."""
    criteria = _criteria(4)
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id="AC-1",
                criterion="Criterion number 1",
                passed=True,
                reasoning="verified",
            ),
        ],
    )
    assert grade_iteration(criteria, output).verdict is AcceptVerdict.rejected


def test_echoed_text_mutation_changes_neither_keying_nor_reinjected_text() -> None:
    """KOD-53/AC-20 — whitespace and backslash mutations in the echo are inert."""
    criteria = as_validated(
        mint_criteria(
            [
                DraftedCriterion(
                    text='The rendered node carries class="kz-row", spaced exactly.',
                ),
                DraftedCriterion(
                    text="A path of the form C:\\\\Users\\\\x survives the round trip.",
                ),
            ]
        )
    )
    mutated = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id="AC-1",
                criterion='The rendered node carries class ="kz-row",spaced exactly.',
                passed=False,
                reasoning="class attribute missing",
            ),
            CriterionResult(
                criterion_id="AC-2",
                criterion="A path of the form C:\\Users\\x survives the round trip.",
                passed=False,
                reasoning="backslashes collapsed",
            ),
        ],
    )

    grade = grade_iteration(criteria, mutated)

    assert grade.missing_ids == []
    assert grade.unknown_ids == []
    assert [f.criterion_id for f in grade.failures] == ["AC-1", "AC-2"]
    assert [f.text for f in grade.failures] == [c.text for c in criteria]
    assert grade.failures[0].text.encode() == criteria[0].text.encode()
    assert grade.failures[1].text.encode() == criteria[1].text.encode()
    # The report is byte-stable too — the echo never reaches a reader.
    assert [r.criterion for r in grade.results] == [c.text for c in criteria]


def test_an_unknown_id_is_discarded_and_a_duplicated_one_grades_failed() -> None:
    criteria = _criteria(2)
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id="AC-1",
                criterion="Criterion number 1",
                passed=True,
                reasoning="verified",
            ),
            CriterionResult(
                criterion_id="AC-1",
                criterion="Criterion number 1",
                passed=False,
                reasoning="second opinion",
            ),
            CriterionResult(
                criterion_id="AC-9",
                criterion="a criterion nobody dispatched",
                passed=True,
                reasoning="invented",
            ),
            CriterionResult(
                criterion_id="AC-2",
                criterion="Criterion number 2",
                passed=True,
                reasoning="verified",
            ),
        ],
    )

    grade = grade_iteration(criteria, output)

    assert grade.duplicate_ids == ["AC-1"]
    assert grade.unknown_ids == ["AC-9"]
    assert grade.missing_ids == []
    # AC-1 came back passed and then failed: the model contradicted itself,
    # so the criterion has no verdict and grades failed rather than letting
    # the first answer stand.
    assert grade.results[0].passed is False
    assert grade.results[0].reasoning == DUPLICATE_RESULT_REASONING
    assert [f.criterion_id for f in grade.failures] == ["AC-1"]
    assert grade.verdict is AcceptVerdict.rejected
    assert grade.dispatched_count == 2
    assert len(grade.results) == 2


def test_results_come_back_in_dispatch_order_whatever_order_they_arrived() -> None:
    criteria = _criteria(3)
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=cid,
                criterion="echo",
                passed=True,
                reasoning="verified",
            )
            for cid in ("AC-3", "AC-1", "AC-2")
        ],
    )
    grade = grade_iteration(criteria, output)
    assert [r.criterion_id for r in grade.results] == ["AC-1", "AC-2", "AC-3"]


def test_an_ungraded_criterion_seats_in_neither_count_nor_feedback() -> None:
    """An ``unverifiable`` criterion is named, never counted, never re-asked.

    Two halves, and the second is the one that costs a run: it is absent
    from ``failures``, which is what the next iteration's feedback is built
    from.  A criterion nothing can grade that recurs every round is the
    budget burn this lane exists to stop, arriving through the back door.
    """
    graded, ungraded_criterion = _criteria(2)
    criteria = [
        graded,
        ungraded_criterion.model_copy(
            update={
                "feasibility": CriterionFeasibility(
                    criterion_id=ungraded_criterion.id,
                    verdict=CriterionVerdict.unverifiable,
                    missing_resource="a PostgreSQL server reachable from the runner",
                ),
            },
        ),
    ]
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id="AC-1",
                criterion="echo",
                passed=True,
                reasoning="verified",
            ),
        ],
    )

    grade = grade_iteration(criteria, output)

    assert grade.passed_count == 1
    assert [f.criterion_id for f in grade.failures] == []
    assert grade.verdict is AcceptVerdict.ship_with_flags


def test_an_ungraded_criterion_answered_as_passing_is_still_not_counted() -> None:
    """Never a pass: the evaluator's answer to it is worth nothing."""
    graded, ungraded_criterion = _criteria(2)
    criteria = [
        graded,
        ungraded_criterion.model_copy(
            update={
                "feasibility": CriterionFeasibility(
                    criterion_id=ungraded_criterion.id,
                    verdict=CriterionVerdict.unverifiable,
                    missing_resource="a PostgreSQL server reachable from the runner",
                ),
            },
        ),
    ]
    output = AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=cid,
                criterion="echo",
                passed=True,
                reasoning="verified",
            )
            for cid in ("AC-1", "AC-2")
        ],
    )

    grade = grade_iteration(criteria, output)

    assert grade.passed_count == 1
    assert grade.verdict is AcceptVerdict.ship_with_flags


# ---------------------------------------------------------------------------
# A price is not an absence — the expensive arm beside the exclusion arm
# (KOD-76/AC-21)
# ---------------------------------------------------------------------------


def _priced_beside_blocked() -> list[ValidatedCriterion]:
    """Two criteria out of one sweep: an expensive one and a blocked one.

    They differ in one thing — the first's demonstration RAN and cost nine
    hours, the second's cannot run at all because a resource the finding
    names is absent.  The verdicts are the sweep's own, so a derivation
    that reads a price as an absence is what this fixture catches.
    """
    criteria = mint_criteria(
        [
            DraftedCriterion(text="The full regression sweep passes at head."),
            DraftedCriterion(text="The migration applies against a live database."),
        ]
    )
    validation = sweep(
        criteria,
        CriteriaValidationOutput(
            findings=[
                CriterionFinding(
                    criterion_id="AC-1",
                    verdict=CriterionVerdict.feasible,
                    smallest_repair=RepairKind.none,
                    cost_claim=CostClaim(
                        assertion="the full sweep is uneconomic to demonstrate",
                        measurement=CostMeasurement(
                            observed="9h of runner time",
                            affordable=False,
                        ),
                    ),
                ),
                CriterionFinding(
                    criterion_id="AC-2",
                    verdict=CriterionVerdict.unverifiable,
                    smallest_repair=RepairKind.environment_supply,
                    missing_resource="a PostgreSQL server reachable from the runner",
                ),
            ],
        ),
    )
    return list(build_artifact(criteria, validation).criteria)


def _answers(*results: tuple[str, bool]) -> AcceptanceCriteriaOutput:
    """One evaluator answer per criterion id, in the order given."""
    return AcceptanceCriteriaOutput(
        criteria_results=[
            CriterionResult(
                criterion_id=criterion_id,
                criterion="echo",
                passed=passed,
                reasoning="verified",
            )
            for criterion_id, passed in results
        ],
    )


def test_an_uneconomic_demonstration_grades_while_a_named_absence_is_excluded() -> None:
    """KOD-76/AC-21 — the expensive arm drives the next iteration, the other does not.

    The expensive criterion fails and is carried into ``failures``, which
    is what the next iteration is driven by; it passes on the second
    grading and is counted.  Neither grading gives the uneconomic claim
    the exclusion the absent resource earns: only the criterion whose
    finding NAMED a resource it lacks seats in neither count nor feedback.
    """
    priced, blocked = _priced_beside_blocked()

    assert priced.feasibility.verdict is CriterionVerdict.feasible
    assert priced.feasibility.verdict is not CriterionVerdict.unverifiable
    assert priced.feasibility.cost_measurement is not None
    assert priced.feasibility.cost_measurement.affordable is False
    assert blocked.feasibility.verdict is CriterionVerdict.unverifiable
    assert blocked.feasibility.missing_resource is not None

    criteria = [priced, blocked]
    failing = grade_iteration(
        criteria, _answers((priced.id, False), (blocked.id, True))
    )

    assert [failure.criterion_id for failure in failing.failures] == [priced.id]
    assert failing.passed_count == 0
    assert failing.verdict is AcceptVerdict.rejected

    passing = grade_iteration(criteria, _answers((priced.id, True), (blocked.id, True)))

    assert [failure.criterion_id for failure in passing.failures] == []
    assert passing.passed_count == 1
    assert passing.verdict is AcceptVerdict.ship_with_flags
