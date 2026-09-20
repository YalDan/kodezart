"""The scope terminal's wire vector, and the lane report facts beside it."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    model_validator,
)

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.scope import ScopeKind, ScopeRef


class LaneReportState(StrEnum):
    """A reported empty gap and a missing report are distinct facts."""

    CONVERGED = "converged"
    IN_GAP = "in_gap"
    HALTED = "halted"
    UNREPORTED = "unreported"


class LaneReport(CamelCaseModel):
    """One dispatched lane, including an explicit value for silence."""

    model_config = ConfigDict(frozen=True)

    lane_key: str = Field(min_length=1, pattern=r"\S")
    issue_id: str = Field(min_length=1, pattern=r"\S")
    state: LaneReportState
    detail: str | None = None


#: The scope kinds whose container carries a status update. A milestone and
#: an issue have no such surface at the backend, so a scope addressed as one
#: of those ends with the terminal event and no write at all.
STATUS_UPDATE_SCOPE_KINDS: frozenset[ScopeKind] = frozenset(
    {ScopeKind.PROJECT, ScopeKind.INITIATIVE}
)


class ScopeLaneEntry(CamelCaseModel):
    """One lane of one reading: its own outcome, and what it recorded.

    ``done`` IS the lane's outcome, and it is read from the criterion
    sub-issues under the lane and from nothing else — not from a fire's
    ending, not from a pull request and not from a merge (KOD-471,
    KOD-480).  ``branch`` and ``pr`` are the two facts the lane's run-state
    record answers "which branch" and "which delivery" with, and both are
    absent for a lane no record addresses yet (KOD-806).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue: str = Field(min_length=1, pattern=r"\S")
    done: bool
    branch: Annotated[str, Field(min_length=1, pattern=r"\S")] | None
    pr: LanePR | None


def derive_scope_outcome(lanes: Sequence[ScopeLaneEntry]) -> WorkflowOutcome:
    """The scope's outcome as arithmetic over the vector and nothing else.

    Two readings only: everything under the scope is done, or the scope is
    still in progress.  An EMPTY vector takes the second, because a report
    about nothing certifies nothing — a scope whose reading offered no lane
    at all has not been shown to be finished, it has been shown to be
    unreadable as finished.
    """
    if lanes and all(lane.done for lane in lanes):
        return WorkflowOutcome.scope_converged
    return WorkflowOutcome.scope_stopped_short


class ScopeTerminalEvent(AgentEvent):
    """One scope walk's clean exit: the per-lane vector and its derivation.

    The scope-level outcome is not authored beside the vector, it IS that
    vector's arithmetic: a value disagreeing with :func:`derive_scope_outcome`
    refuses here rather than reaching a consumer, so no wire snapshot can
    carry a finished claim its own entries do not support (KOD-471).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["scope_terminal"] = "scope_terminal"
    scope: ScopeRef
    lanes: tuple[ScopeLaneEntry, ...]
    outcome: WorkflowOutcome

    @model_validator(mode="after")
    def _each_lane_appears_once(self) -> Self:
        """One entry per lane, so a repeated issue cannot dilute the vector."""
        keys = [lane.issue for lane in self.lanes]
        if len(set(keys)) != len(keys):
            raise ValueError("one entry per lane: an issue cannot appear twice")
        return self

    @model_validator(mode="after")
    def _outcome_is_the_derivation(self) -> Self:
        """The outcome the vector derives to, and no other member of the enum."""
        if self.outcome is not derive_scope_outcome(self.lanes):
            raise ValueError(
                "the scope outcome must be the derivation of its own lane vector"
            )
        return self
