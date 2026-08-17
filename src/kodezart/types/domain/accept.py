"""The accept gate's three-state verdict and the items a flagged run carries.

The boolean this replaced carried two decisions in one value: whether the
work ships, and whether anything about it needs saying.  A run whose only
failures are soft signals is both.  ``ship_with_flags`` routes to the same
merge and pull request as ``accepted`` and carries the flagged items into
the pull-request body, where a human reads them.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criteria import CriterionId


class AcceptVerdict(StrEnum):
    """Three-way partition of one evaluation pass. Never a boolean."""

    accepted = "accepted"
    ship_with_flags = "ship_with_flags"
    rejected = "rejected"


class SherlockFlag(CamelCaseModel):
    """A reasoning concern the evaluator raised in its own name.

    ``[sherlock]`` findings are the concerns no single Watson owns, raised
    by the synthesis over their combined reports.  They used to live inside
    ``reasoning`` prose, where nothing downstream could read them.

    ``criterion_id`` is ``None`` when the concern is about the SET rather
    than one criterion; the absence is meaningful, not a value to fill in.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId | None = Field(
        default=None,
        description=(
            "The criterion this concern is about; absent when the concern is "
            "about the set rather than one criterion."
        ),
    )
    concern: str = Field(
        min_length=1,
        description=(
            "The reasoning concern, stated so a downstream reader can act on it."
        ),
    )


class FlaggedItem(CamelCaseModel):
    """One item a ``ship_with_flags`` run must state in its pull request."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId | None = None
    summary: str = Field(min_length=1)
