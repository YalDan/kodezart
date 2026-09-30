"""What two gradings of one roster say about the checks that produced them."""

from kodezart.domain.mutation_survival import passing_checks, surviving_checks
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionId

WIRED = CriterionId("AC-wired")
TAUTOLOGY = CriterionId("AC-tautology")
BROKEN = CriterionId("AC-broken")


def result(key: CriterionId, *, passed: bool) -> CriterionResult:
    return CriterionResult(
        criterion_id=key,
        criterion=f"the check {key} names",
        passed=passed,
        reasoning="Ran the check.",
    )


def test_a_check_that_passed_in_both_trees_survived():
    """Passing with the behaviour gone is no reading of that behaviour."""
    clean = [result(WIRED, passed=True), result(TAUTOLOGY, passed=True)]
    mutated = [result(WIRED, passed=False), result(TAUTOLOGY, passed=True)]

    assert surviving_checks(clean=clean, mutated=mutated) == {TAUTOLOGY}


def test_a_check_the_mutant_grading_failed_is_not_a_survivor():
    """A failure there is all it is: the verdict stays the clean tree's.

    Which behaviour broke it is not asked, because nothing rests on it — the
    only conclusion the comparison draws is the withholding one.
    """
    clean = [result(WIRED, passed=True)]
    mutated = [result(WIRED, passed=False)]

    assert surviving_checks(clean=clean, mutated=mutated) == frozenset()


def test_a_check_the_mutant_grading_does_not_answer_for_is_not_a_survivor():
    """A reading nobody took withholds nothing."""
    clean = [result(WIRED, passed=True), result(TAUTOLOGY, passed=True)]
    mutated = [result(WIRED, passed=True)]

    assert surviving_checks(clean=clean, mutated=mutated) == {WIRED}
    assert surviving_checks(clean=clean, mutated=[]) == frozenset()


def test_only_the_passing_criteria_can_be_withheld():
    """A criterion that already failed has no pass to take away.

    So a mutant grading that passes it says nothing that could change its
    verdict, in either direction.
    """
    clean = [result(WIRED, passed=True), result(BROKEN, passed=False)]
    mutated = [result(WIRED, passed=True), result(BROKEN, passed=True)]

    assert passing_checks(clean) == {WIRED}
    assert surviving_checks(clean=clean, mutated=mutated) == {WIRED}
