"""Point-in-time scope execution observations, never terminal judgments."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import AgentEvent, ErrorEvent, NativeFireProgressEvent
from kodezart.types.domain.dispatch import IssueExclusion
from kodezart.types.domain.native_delivery import LaneDeliveryEvent
from kodezart.types.domain.scope import ScopeRef


class LaneFailure(CamelCaseModel):
    """One lane's own failure, as the walk that contained it reports it.

    The error is the same typed egress value a job failure carries, so a
    reader tells one lane's fault from the walk's by WHERE it is reported
    and not by how it is shaped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_key: str = Field(min_length=1)
    error: ErrorEvent


class ScopeWalkObservation(CamelCaseModel):
    """Facts from a fresh walk, including obligations selection cannot discharge.

    An empty ready set says only that this invocation can launch nothing.
    It does not establish scope convergence or settle residual ownership.

    ``failed_lanes`` carries the lanes whose own work raised. A walk that
    contains one lane's failure reports it here and keeps going, so an empty
    ready set with entries here is a walk that stopped offering lanes rather
    than a scope at rest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeRef
    tick: int = Field(ge=1)
    ready: tuple[str, ...]
    dispatched: tuple[str, ...]
    skipped_lanes: tuple[str, ...] = ()
    failed_lanes: tuple[LaneFailure, ...] = ()
    unresolved_criteria: tuple[str, ...]
    unapproved_lanes: tuple[str, ...]
    exclusions: tuple[IssueExclusion, ...]


class ScopeWalkEvent(AgentEvent):
    """A scope job reports observations separately from an inner fire's terminal."""

    type: Literal["scope_walk"] = "scope_walk"
    observation: ScopeWalkObservation


type ScopeLaneProgress = Annotated[
    NativeFireProgressEvent | LaneDeliveryEvent, Field(discriminator="type")
]


class ScopeLaneEvent(AgentEvent):
    """Lane identity accompanies progress without creating another queue job."""

    type: Literal["scope_lane"] = "scope_lane"
    lane_key: str = Field(min_length=1)
    event: ScopeLaneProgress
