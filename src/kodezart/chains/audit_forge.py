"""Compare a completed criterion's Evidence SHA with actual forge observations."""

import asyncio
from typing import assert_never

from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    CIMonitor,
    CriterionResolver,
)
from kodezart.domain.check_chain import counted_checks, rostered_forge_checks
from kodezart.domain.errors import AuditClaimReadError, AuditEvidenceReadError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.audit_failures import AUDIT_READ_FAILURES, parse_audit_evidence
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_forge import AuditForgeObservation, AuditForgeRequest
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    IncompleteChecks,
    ObservedChecks,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class AuditForgeVerifier:
    """Consume existing CI capabilities and the delivery lane's sole classifier.

    A separate task prevents a prior rerun attempt in the caller's task from
    selecting this observation's backend attempt. Completed evidence returns
    directly from the watch and can be read in any task.
    The classifier can request its declared bounded same-SHA reruns. This
    consumer performs no tracker correction, remediation or publication.
    """

    def __init__(
        self,
        *,
        resolver: CriterionResolver,
        ci: CIMonitor | None,
        operation: OperationConfig,
        config: AppConfig,
    ) -> None:
        self._resolver = resolver
        self._ci = ci
        self._operation = operation
        self._config = config

    def _repository(self, repo_url: str) -> RepoEntry:
        normalized = resolve_repo_url(repo_url, self._config.git.base_url)
        matches = [
            entry
            for entry in self._operation.repos
            if resolve_repo_url(entry.url, self._config.git.base_url) == normalized
        ]
        if len(matches) != 1:
            raise AuditClaimReadError("a unique repository declaration is required")
        return RepoEntry.model_validate({**matches[0].model_dump(), "url": repo_url})

    async def observe(self, request: AuditForgeRequest) -> AuditForgeObservation:
        try:
            criterion = await self._resolver.resolve_criterion(
                issue_key=request.lane_issue_key,
                criterion_key=request.criterion_key,
            )
            if criterion.state_kind is not WorkflowStateKind.COMPLETED:
                raise AuditClaimReadError(
                    "forge verification requires a completed criterion"
                )
            evidence = parse_audit_evidence(criterion.body)
            observed = await asyncio.create_task(
                self._forge(request=request, criterion=criterion, evidence=evidence)
            )
            if criterion != await self._resolver.resolve_criterion(
                issue_key=request.lane_issue_key,
                criterion_key=request.criterion_key,
            ):
                raise AuditClaimReadError(
                    "the criterion changed during forge verification"
                )
            return observed
        except AUDIT_READ_FAILURES as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key, reason=str(exc)
            ) from exc

    async def _forge(
        self,
        *,
        request: AuditForgeRequest,
        criterion: TrackerIssue,
        evidence: CriterionEvidence,
    ) -> AuditForgeObservation:
        required: frozenset[str] = frozenset()
        checks: ObservedChecks | None = None
        # What the arm leaves out of what was reported: the reading of
        # whatever ``checks`` holds, recomputed beside every assignment of it
        # and nowhere else, because the record refuses an exclusion set that
        # disagrees with the roster it was reported in.
        excluded: frozenset[str] = frozenset()
        red: CheckRedObservation | None = None

        def result(verdict: AuditVerdict, reason: str) -> AuditForgeObservation:
            return AuditForgeObservation(
                criterion=criterion,
                recorded_evidence=evidence,
                required_check_names=required,
                excluded_check_names=excluded,
                checks=checks,
                red=red,
                verdict=verdict,
                reason=f"{request.criterion_key} at {evidence.graded_sha}: {reason}",
            )

        try:
            repository = self._repository(request.repo_url)
            required = rostered_forge_checks(repository.checks)
            if self._ci is None:
                raise AuditClaimReadError(
                    "the forge check capabilities are unavailable"
                )
            watched = await self._ci.wait_for_checks(
                repo_url=request.repo_url, ref=evidence.graded_sha
            )
            if isinstance(watched, AbsentChecks):
                return result(AuditVerdict.UNVERIFIABLE, "no run at the recorded SHA")
            if isinstance(watched, IncompleteChecks):
                raise AuditClaimReadError(watched.summary)
            checks = self._checked_snapshot(watched, evidence)
            counted = counted_checks(
                reported=checks.check_names,
                failed=checks.failed_check_names,
                rostered=required,
            )
            excluded = counted.excluded
            if not counted.failures:
                self._require_roster(checks, required)
                return result(
                    AuditVerdict.HOLDS, "the rostered checks are green at that SHA"
                )
            red = await classify_red_checks(
                ci=self._ci,
                repository=repository,
                repo_url=repository.url,
                initial=checks,
                max_attempts=self._config.delivery_red_rerun_max_attempts,
                counted=required or None,
            )
            if isinstance(red.observation, AbsentChecks):
                return result(
                    AuditVerdict.UNVERIFIABLE,
                    "the classified rerun has no completed check observation",
                )
            checks = self._checked_snapshot(red.observation, evidence)
            counted = counted_checks(
                reported=checks.check_names,
                failed=checks.failed_check_names,
                rostered=required,
            )
            excluded = counted.excluded
            match red.red_class:
                case CheckRedClass.RUNNER_FLAKE:
                    self._require_roster(checks, required)
                    return result(
                        AuditVerdict.HOLDS,
                        "a same-SHA rerun is green in the rostered checks",
                    )
                case CheckRedClass.WORK_DEFECT:
                    return result(AuditVerdict.REFUTED, "same-SHA checks reproduce red")
                case CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET:
                    return result(
                        AuditVerdict.UNVERIFIABLE, "a declared prerequisite is unmet"
                    )
                case CheckRedClass.UNCLASSIFIED:
                    return result(
                        AuditVerdict.UNVERIFIABLE, "red checks are unclassified"
                    )
                case _ as unreachable:
                    assert_never(unreachable)
        except AUDIT_READ_FAILURES as exc:
            return result(AuditVerdict.UNVERIFIABLE, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _checked_snapshot(
        checks: ObservedChecks,
        evidence: CriterionEvidence,
    ) -> ObservedChecks:
        if checks.commit_sha != evidence.graded_sha:
            raise AuditClaimReadError(
                "the completed observation differs from the requested SHA"
            )
        return checks

    @staticmethod
    def _require_roster(checks: ObservedChecks, required: frozenset[str]) -> None:
        if not required <= checks.check_names:
            missing = sorted(required - checks.check_names)
            raise AuditClaimReadError(f"configured checks were not observed: {missing}")
