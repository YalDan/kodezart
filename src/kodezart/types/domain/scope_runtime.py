"""Point-in-time scope execution observations, never terminal judgments."""

from typing import Literal

from pydantic import ConfigDict, Field, SerializeAsAny

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.dispatch import IssueExclusion
from kodezart.types.domain.scope import ScopeRef


class ScopeWalkObservation(CamelCaseModel):
    """Facts from a fresh walk, including obligations selection cannot discharge.

    An empty ready set says only that this invocation can launch nothing.
    It does not establish scope convergence or settle residual ownership.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeRef
    tick: int = Field(ge=1)
    ready: tuple[str, ...]
    dispatched: tuple[str, ...]
    skipped_lanes: tuple[str, ...] = ()
    unresolved_criteria: tuple[str, ...]
    unapproved_lanes: tuple[str, ...]
    exclusions: tuple[IssueExclusion, ...]


class ScopeWalkEvent(AgentEvent):
    """A scope job reports observations separately from an inner fire's terminal."""

    type: Literal["scope_walk"] = "scope_walk"
    observation: ScopeWalkObservation


class ScopeLaneEvent(AgentEvent):
    """Lane identity accompanies progress without creating another queue job."""

    type: Literal["scope_lane"] = "scope_lane"
    lane_key: str = Field(min_length=1)
    event: SerializeAsAny[AgentEvent]
