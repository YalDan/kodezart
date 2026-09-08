"""Inputs and point-in-time coverage observations for the audit cadence."""

from pydantic import AwareDatetime, ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope import ScopeRef


class AuditCandidate(CamelCaseModel):
    """An eligible issue's own state-change stamp, supplied by its reader."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_key: str = Field(min_length=1, pattern=r"\S")
    state_changed_at: AwareDatetime


class AuditCoverageResult(CamelCaseModel):
    """What this invocation actually covered, never a durable verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeRef
    observed_at: AwareDatetime
    full: bool
    covered: tuple[AuditCandidate, ...]
