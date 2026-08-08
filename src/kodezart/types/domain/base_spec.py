"""The lane's recorded base — the one ref every scope check compares against.

A scope or no-touch criterion asks *which files did this lane change*, and
that question has no answer without a baseline.  Taking the repository's
trunk as that baseline is wrong for any lane built on another lane's
work: everything inherited from the base reads as this lane's own change,
and a criterion that says "touch only these files" then convicts the lane
of edits it never made — which is how correct, already-graded work gets
reverted.

The baseline is therefore the lane's RECORDED base, and this module is
the shape that value has when it reaches the check.  It is a value the
lane is HANDED, never one the check derives: base resolution — reading
the blockers, resolving each to its deliverable ref, reducing to the
frontier and combining — belongs to the issue that owns the association,
and a second derivation here would be a second source of truth with a
shorter half-life than the graph it mirrors.

Two things follow, and both are asserted rather than intended:

* **A branch name is not a record.**  Nothing here parses a ref to learn
  what it is; the role is carried, because only the association knows it.
* **The inputs travel with the base.**  A base computed from blockers
  that have since moved is stale, and staleness is decided by comparing
  this value with the one the blockers imply now — which is possible only
  because the inputs it was computed from are part of it.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class BaseRefRole(StrEnum):
    """What the recorded base ref IS — carried, never inferred.

    ``trunk`` is the scope's configured trunk, taken when the lane has no
    blockers at all.  ``deliverable`` is a single blocker's own branch.
    ``integration`` is a ref constructed to combine several blockers.
    The scope check treats all three identically and reads the role only
    in order to report it.
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

    ``inputs`` is empty exactly on the trunk arm — a trunk base is
    computed from no blocker.  On every other arm it is the ordered tuple
    the base was built from, which is what makes staleness arithmetic
    rather than judgement.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    base_ref: str = Field(min_length=1)
    role: BaseRefRole
    inputs: tuple[BaseInput, ...] = ()


def trunk_base(branch: str) -> BaseSpec:
    """The base a lane with no blockers has: the trunk it was fired against."""
    return BaseSpec(base_ref=branch, role=BaseRefRole.trunk, inputs=())
