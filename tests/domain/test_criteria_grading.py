"""Grading is keyed by id and reconciled against the dispatched set."""

from kodezart.domain.criteria import mint_criteria
from kodezart.domain.criteria_grading import MISSING_RESULT_REASONING, grade_iteration
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, CriterionResult
from kodezart.types.domain.criteria import CriterionClassification, DraftedCriterion


def _criteria(count: int) -> list:
    return list(
        mint_criteria(
            [
                DraftedCriterion(
                    text=f"Criterion number {n}",
                    classification=CriterionClassification.hard_gate,
                )
                for n in range(1, count + 1)
            ]
        )
    )


def test_partial_return_grades_the_missing_ids_failed_and_keeps_the_denominator() -> (
    None
):
    """3 of 10 dispatched ids answered → 7 fail, denominator stays 10."""
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
    """Whitespace and backslash mutations in the echo are inert."""
    criteria = list(
        mint_criteria(
            [
                DraftedCriterion(
                    text='The rendered node carries class="kz-row", spaced exactly.',
                    classification=CriterionClassification.hard_gate,
                ),
                DraftedCriterion(
                    text="A path of the form C:\\\\Users\\\\x survives the round trip.",
                    classification=CriterionClassification.hard_gate,
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
