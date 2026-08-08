"""The graded outcome of one evaluation pass over a dispatched criteria set.

Grading is reconciled against the DISPATCHED id set, never against
whatever the evaluator chose to return.  The denominator is fixed by the
harness, a missing id grades failed and is named, and no partial return
can produce acceptance over a shorter list.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionFailure


class IterationGrade(CamelCaseModel):
    """One evaluation pass, reconciled to the dispatched ids."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    results: list[CriterionResult] = Field(min_length=1)
    failures: list[CriterionFailure] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    dispatched_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    accepted: bool
