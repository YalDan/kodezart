"""The graded outcome of one evaluation pass over a dispatched criteria set.

Reconciled against the DISPATCHED id set, never against whatever the
evaluator returned: the denominator is the harness's, a missing id grades
failed and is named, and no partial return produces acceptance over a
shorter list.

An ``unverifiable`` criterion is exempt from numerator and denominator
alike — never a pass, never a fail — and its presence clamps the verdict
ceiling to ``ship_with_flags``.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict, SherlockFlag
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionFailure, CriterionIdItem


class IterationGrade(CamelCaseModel):
    """One evaluation pass, reconciled to the dispatched ids."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    results: list[CriterionResult] = Field(min_length=1)
    failures: list[CriterionFailure] = Field(default_factory=list)
    missing_ids: list[CriterionIdItem] = Field(default_factory=list)
    unknown_ids: list[CriterionIdItem] = Field(default_factory=list)
    duplicate_ids: list[CriterionIdItem] = Field(default_factory=list)
    dispatched_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    verdict: AcceptVerdict
    sherlock_flags: list[SherlockFlag] = Field(default_factory=list)
