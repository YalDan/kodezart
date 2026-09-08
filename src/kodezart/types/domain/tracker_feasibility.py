"""Explicit source and observations for a tracker feasibility session."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criteria import (
    ContractCorrection,
    DerivedFeasibility,
    TrackerCriteriaValidationOutput,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.tracker import TrackerIssue


class TrackerFeasibilityRequest(CamelCaseModel):
    """A tracker address and already-resolved dispatch head, without spec text."""

    model_config = ConfigDict(frozen=True)

    issue_key: str = Field(min_length=1, pattern=r"\S")
    repo_url: str = Field(min_length=1, pattern=r"\S")
    head_sha: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None


class TrackerFeasibilityObservation(CamelCaseModel):
    """Caller-owned source facts beside the native-key session judgment.

    A completed observation authorizes no tracker state transition or fire
    dispatch. None means the full current family contains no Todo criteria.
    """

    model_config = ConfigDict(frozen=True)

    spec: TrackerSpec
    criteria: tuple[TrackerIssue, ...]
    head_sha: str
    judgment: TrackerCriteriaValidationOutput | None
    derivations: tuple[DerivedFeasibility, ...]
    correction: ContractCorrection | None
