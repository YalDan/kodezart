"""Current terminal-state evidence, separate from historical verdicts."""

import json
from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditMandateObservation, AuditVerdict
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

    def defect_class(self) -> str:
        """Name the observed discrepancy set without inventing criterion identity."""
        return f"terminal state inconsistency on issue {self.issue_key}: " + ", ".join(
            item.value for item in self.discrepancies
        )

    def refutation_evidence(self) -> str:
        """Supply native identity and current facts, excluding any recorded verdict."""
        return json.dumps(
            self.model_dump(mode="json", exclude={"verdict"}), ensure_ascii=False
        )


class AuditTerminalReport(CamelCaseModel):
    """The original issue-terminal read with its required mandate observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation: AuditTerminalObservation
    mandate: AuditMandateObservation | None

    @model_validator(mode="after")
    def _refutation_requires_mandate(self) -> Self:
        observed = self.observation
        if (observed.verdict is AuditVerdict.REFUTED) != (self.mandate is not None):
            raise ValueError("every terminal refutation requires its mandate verdict")
        if observed.verdict is AuditVerdict.REFUTED:
            if not observed.branch_head or not observed.discrepancies:
                raise ValueError(
                    "terminal mandate completion requires observed head and discrepancy"
                )
            if (
                self.mandate is not None
                and self.mandate.finding is not None
                and self.mandate.finding.defect_class != observed.defect_class()
            ):
                raise ValueError("terminal mandate finding names another defect")
        return self
