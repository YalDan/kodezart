"""Current terminal-state evidence, separate from historical verdicts."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.pr_state import PRState


class TerminalDiscrepancy(StrEnum):
    NO_BRANCH = "no_branch"
    CLOSED_UNMERGED_PR = "closed_unmerged_pr"
    UNRESOLVED_ASSOCIATION = "unresolved_association"


class AuditTerminalRequest(CamelCaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    issue_key: str = Field(min_length=1)
    lane_key: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    cache_key: str | None = None
    record_ref: str | None = None


class AuditTerminalObservation(CamelCaseModel):
    """A settled read, not an authorization to change tracker state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    issue_key: str
    record_ref: str
    verdict: AuditVerdict
    discrepancies: tuple[TerminalDiscrepancy, ...]
    branch_head: str | None
    pr: PRState | None
