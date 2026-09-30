"""Exact-SHA forge observations of a criterion's recorded grading claim."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.domain.check_chain import counted_checks
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.check_observation import ObservedChecks
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.fire_spec import CriterionRef
from kodezart.types.domain.tracker import TrackerIssue


class AuditForgeRequest(CamelCaseModel):
    """Select an owning native criterion; its SHA is read, never supplied here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_key: CriterionRef = Field(min_length=1)
    lane_issue_key: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)


class AuditForgeObservation(CamelCaseModel):
    """The forge proposition, before mandate completion or any tracker write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: TrackerIssue
    recorded_evidence: CriterionEvidence
    required_check_names: frozenset[str]
    excluded_check_names: frozenset[str] = frozenset()
    checks: ObservedChecks | None
    red: CheckRedObservation | None
    verdict: AuditVerdict
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def verdict_has_exact_observation(self) -> Self:
        if self.checks is not None and (
            self.checks.commit_sha != self.recorded_evidence.graded_sha
        ):
            raise ValueError("the checks belong to another grading SHA")
        counted = counted_checks(
            reported=frozenset() if self.checks is None else self.checks.check_names,
            failed=(
                frozenset() if self.checks is None else self.checks.failed_check_names
            ),
            rostered=self.required_check_names,
        )
        if self.checks is None:
            if self.excluded_check_names:
                raise ValueError(
                    "an excluded check requires the roster it was reported in"
                )
        elif self.excluded_check_names != counted.excluded:
            raise ValueError(
                "the excluded checks are not the ones this roster leaves out"
            )
        if self.verdict is AuditVerdict.UNVERIFIABLE:
            return self
        # The red's own snapshot is read through the same roster: a rerun red
        # only in a check the arm leaves out is green for the claim.
        red_failures = (
            counted_checks(
                reported=self.red.observation.check_names,
                failed=self.red.observation.failed_check_names,
                rostered=self.required_check_names,
            ).failures
            if self.red is not None and isinstance(self.red.observation, ObservedChecks)
            else None
        )
        if self.checks is None or not self.checks.check_names:
            raise ValueError("a definite verdict requires the observed check roster")
        if self.verdict is AuditVerdict.HOLDS:
            if not self.required_check_names <= self.checks.check_names:
                raise ValueError(
                    "a clean verdict requires the complete declared roster"
                )
            if counted.failures or (
                self.red is not None
                and (
                    self.red.red_class is not CheckRedClass.RUNNER_FLAKE
                    or red_failures is None
                    or red_failures
                )
            ):
                raise ValueError("a clean forge claim requires a green exact-SHA run")
        elif (
            not counted.failures
            or self.red is None
            or self.red.red_class is not CheckRedClass.WORK_DEFECT
            or not red_failures
        ):
            raise ValueError("a refuted forge claim requires classified work failure")
        return self
