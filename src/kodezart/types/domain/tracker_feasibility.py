"""Explicit source and observations for a tracker feasibility session."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.criteria import (
    ContractCorrection,
    DerivedFeasibility,
    TrackerCriteriaValidationOutput,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.tracker import TrackerIssue


class TrackerFeasibilityRequest(CamelCaseModel):
    """A tracker address and already-resolved dispatch head, without spec text."""

    model_config = ConfigDict(frozen=True)

    issue_key: str = Field(min_length=1, pattern=r"\S")
    repo_url: str = Field(min_length=1, pattern=r"\S")
    head_sha: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None
    run_identity: RunIdentity | None = None

    @model_validator(mode="after")
    def own_fire_identity(self) -> Self:
        if self.run_identity is not None and (
            self.run_identity.kind is not RunKind.FIRE
            or self.run_identity.name != self.issue_key
        ):
            raise ValueError("the fire identity must name the addressed issue")
        return self


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
