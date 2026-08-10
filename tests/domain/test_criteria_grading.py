"""Grading is keyed by id and reconciled against the dispatched set.

KOD-53/AC-19 (a partial return grades fail-closed over the dispatched
denominator) and KOD-53/AC-20 (an echoed-text mutation changes neither the
keying nor the text carried forward) are demonstrated here.
"""

from kodezart.domain.criteria import mint_criteria
from kodezart.domain.criteria_grading import MISSING_RESULT_REASONING, grade_iteration
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import (
    CriterionClass,
    CriterionFeasibility,
    CriterionVerdict,
    DraftedCriterion,
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
                DraftedCriterion(
                    text=f"Criterion number {n}",
                    criterion_class=CriterionClass.hard_gate,
                )
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
                    criterion_class=CriterionClass.hard_gate,
                ),
                DraftedCriterion(
                    text="A path of the form C:\\\\Users\\\\x survives the round trip.",
                    criterion_class=CriterionClass.hard_gate,
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


def test_unknown_and_duplicate_ids_are_discarded_and_named() -> None:
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
    # The FIRST answer for an id stands; the duplicate never overrides it.
    assert grade.results[0].passed is True
    assert grade.verdict is AcceptVerdict.accepted
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

    assert grade.ungraded_criterion_ids == ["AC-2"]
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
