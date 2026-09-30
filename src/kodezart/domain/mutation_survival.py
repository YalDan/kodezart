"""What a check is worth when the behaviour it names is gone from the tree.

Arithmetic over two gradings, and nothing else: which criteria a grading
passed, and which of those passed again in a tree the behaviour they name
had been removed from.  The judgement is the evaluation that produced each
grading; comparing the two is a plain function.
"""

from collections.abc import Sequence

from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionId


def passing_checks(results: Sequence[CriterionResult]) -> frozenset[CriterionId]:
    """The criteria this grading passed — the only ones a removal can withhold.

    A criterion that already failed has no pass to take away, so a reading
    that says nothing about it changes nothing.
    """
    return frozenset(result.criterion_id for result in results if result.passed)


def surviving_checks(
    *, clean: Sequence[CriterionResult], mutated: Sequence[CriterionResult]
) -> frozenset[CriterionId]:
    """The criteria that passed in BOTH trees.

    Passing in both is no reading of the behaviour that was removed.  A
    criterion the mutant grading does not answer for is not a survivor: a
    reading nobody took withholds nothing.  A criterion the mutant grading
    fails is not a survivor either, and that is all a failure there proves —
    which behaviour broke it is not asked, because nothing rests on it.
    """
    return passing_checks(clean) & passing_checks(mutated)
