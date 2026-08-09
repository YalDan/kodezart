"""The graded outcome of one evaluation pass over a dispatched criteria set.

Grading is reconciled against the DISPATCHED id set, never against
whatever the evaluator chose to return.  The denominator is fixed by the
harness, a missing id grades failed and is named, and no partial return
can produce acceptance over a shorter list.

One kind of criterion is exempt from that denominator and from the
numerator alike: an ``unverifiable`` one, whose demonstration the sweep
established the runner cannot perform.  It is never a pass and never a
fail, so ``passed_count`` and ``failures`` pass over it and
``ungraded_criterion_ids`` names it instead — the presence of that list
is what clamps the verdict ceiling to ``ship_with_flags``.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict, SherlockFlag
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionFailure, CriterionId


class IterationGrade(CamelCaseModel):
    """One evaluation pass, reconciled to the dispatched ids."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    results: list[CriterionResult] = Field(min_length=1)
    failures: list[CriterionFailure] = Field(default_factory=list)
    missing_ids: list[CriterionId] = Field(default_factory=list)
    unknown_ids: list[CriterionId] = Field(default_factory=list)
    duplicate_ids: list[CriterionId] = Field(default_factory=list)
    dispatched_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    verdict: AcceptVerdict
    sherlock_flags: list[SherlockFlag] = Field(default_factory=list)
    ungraded_criterion_ids: list[CriterionId] = Field(default_factory=list)
