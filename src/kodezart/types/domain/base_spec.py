"""The lane's recorded base — the one ref every scope check compares against.

A scope criterion asks *which files did this lane change*, and that has no
answer without a baseline.  Measured: with trunk as the baseline, every
file a stacked lane inherited read as its own change, so a "touch only
these files" criterion convicted the lane of edits it never made and
already-graded work was reverted.

The base is HANDED to the check, never derived by it — resolving blockers
to a ref belongs to the issue that owns the association.  The inputs it
was computed from travel with it, which is what makes staleness a
comparison rather than a judgement.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class BaseRefRole(StrEnum):
    """What the recorded base ref IS — carried, never inferred.

    ``trunk`` when the lane has no blockers, ``deliverable`` for a single
    blocker's own branch, ``integration`` for a ref combining several.
    The scope check treats all three alike and reads the role to report it.
    """

    trunk = "trunk"
    deliverable = "deliverable"
    integration = "integration"


class BaseInput(CamelCaseModel):
    """One blocker's contribution to a base, pinned at a sha."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    blocker_issue_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    sha: str = Field(min_length=7)


class BaseSpec(CamelCaseModel):
    """The recorded base ref, its role, and the inputs it was computed from.

    ``inputs`` is empty exactly on the trunk arm; on every other arm it is
    the ordered tuple the base was built from.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    base_ref: str = Field(min_length=1)
    role: BaseRefRole
    inputs: tuple[BaseInput, ...] = ()


def trunk_base(branch: str) -> BaseSpec:
    """The base a lane with no blockers has: the trunk it was fired against."""
    return BaseSpec(base_ref=branch, role=BaseRefRole.trunk, inputs=())
