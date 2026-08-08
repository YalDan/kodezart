"""The accept gate's three-state verdict and the items a flagged run carries.

``AcceptVerdict`` replaces the boolean the loop used to compute.  The
boolean could only say *every criterion passed* or *something did not*,
which collapsed two decisions into one: whether the work is shippable,
and whether anything about it needs saying.  A run whose only failures
are soft signals is shippable AND has something to say, and there was no
value that could carry both.

Three members, and the count is a contract.  ``ship_with_flags`` is not a
degraded ``accepted``: it routes to the same merge and pull request, and
it carries the flagged items into the pull-request body so the thing that
failed is legible to the human who reads it rather than lost in a log.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class AcceptVerdict(StrEnum):
    """Three-way partition of one evaluation pass. Never a boolean."""

    accepted = "accepted"
    ship_with_flags = "ship_with_flags"
    rejected = "rejected"


class SherlockFlag(CamelCaseModel):
    """A reasoning concern the evaluator raised in its own name.

    ``[sherlock]`` findings are the concerns no single Watson owns —
    raised by the synthesis over their combined reports.  They used to
    live inside ``reasoning`` prose, where nothing downstream could read
    them; the field exists so they are carried as data.

    ``criterion_id`` is ``None`` when the concern is about the set rather
    than about one criterion, and that absence is meaningful — it is not
    a missing value to be filled in.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str | None = None
    concern: str = Field(min_length=1)


class FlaggedItem(CamelCaseModel):
    """One item a ``ship_with_flags`` run must state in its pull request."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str | None = None
    summary: str = Field(min_length=1)
