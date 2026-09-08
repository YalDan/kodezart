"""Inputs and point-in-time coverage observations for the audit cadence."""

from enum import StrEnum

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


class AuditVerdict(StrEnum):
    """Evidence supports, refutes, or cannot settle a claim."""

    HOLDS = "holds"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AuditVerdict requires an explicit three-state comparison")


class AuditClaimJudgment(CamelCaseModel):
    """One fresh session's judgment, before mandate completion or publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Criterion whose current Check was examined.",
    )
    verdict: AuditVerdict = Field(
        description="Holds, refuted or unverifiable from fresh repository evidence."
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Re-execution evidence, counterexample or missing resource.",
    )


class AuditClaimObservation(CamelCaseModel):
    """Harness-owned identity of the exact source and head actually examined."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgment: AuditClaimJudgment
    head_sha: str = Field(min_length=1)
    record_ref: str = Field(min_length=1)
    check: str = Field(min_length=1)


class AuditClaimRequest(CamelCaseModel):
    """Explicit subject and repository binding, with no prior-session input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: str = Field(min_length=1, pattern=r"\S")
    lane_issue_key: str = Field(min_length=1, pattern=r"\S")
    lane_key: str = Field(min_length=1, pattern=r"\S")
    repo_url: str = Field(min_length=1, pattern=r"\S")
    cache_key: str | None = None
    record_ref: str | None = None
