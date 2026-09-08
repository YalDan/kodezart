"""Compare a completed criterion's Evidence SHA with actual forge observations."""

import asyncio
from typing import assert_never

from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    CIMonitor,
    CIObservationReader,
    TrackerCriteriaReader,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.check_classification import classify_red_checks
from kodezart.services.criterion_sources import resolve_criterion
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_forge import AuditForgeObservation, AuditForgeRequest
from kodezart.types.domain.check_observation import ObservedChecks
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.delivery import CheckRedClass, CheckRedObservation
from kodezart.types.domain.operation import OperationConfig, RepoEntry
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class AuditForgeVerifier:
    """Consume existing CI capabilities and the delivery lane's sole classifier.

    A separate task per observation prevents prior monitor attempts or watch
    snapshots in the caller's task from supplying this observation's evidence.
    The classifier can request its declared bounded same-SHA reruns. This
    consumer performs no tracker correction, remediation or publication.
    """

    def __init__(
        self,
        *,
        tracker: TrackerCriteriaReader,
        ci: CIMonitor | None,
        observations: CIObservationReader | None,
        operation: OperationConfig,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._ci = ci
        self._observations = observations
        self._operation = operation
        self._config = config

    def _repository(self, repo_url: str) -> RepoEntry:
        normalized = resolve_repo_url(repo_url, self._config.git_base_url)
        matches = [
            entry
            for entry in self._operation.repos
            if resolve_repo_url(entry.url, self._config.git_base_url) == normalized
        ]
        try:
            (repository,) = matches
        except ValueError as exc:
            raise ValueError("a unique repository declaration is required") from exc
        return repository.model_copy(deep=True, update={"url": repo_url})

    async def observe(self, request: AuditForgeRequest) -> AuditForgeObservation:
        try:
            criterion = await resolve_criterion(
                tracker=self._tracker,
                issue_key=request.lane_issue_key,
                criterion_key=request.criterion_key,
            )
            if criterion.state_kind is not WorkflowStateKind.COMPLETED:
                raise ValueError("forge verification requires a completed criterion")
            evidence = parse_criterion_evidence(criterion.body)
            observed = await asyncio.create_task(
                self._forge(request=request, criterion=criterion, evidence=evidence)
            )
            if criterion != await resolve_criterion(
                tracker=self._tracker,
                issue_key=request.lane_issue_key,
                criterion_key=request.criterion_key,
            ):
                raise ValueError("the criterion changed during forge verification")
            return observed
        except Exception as exc:
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
        red: CheckRedObservation | None = None

        def result(verdict: AuditVerdict, reason: str) -> AuditForgeObservation:
            return AuditForgeObservation(
                criterion=criterion,
                recorded_evidence=evidence,
                required_check_names=required,
                checks=checks,
                red=red,
                verdict=verdict,
                reason=f"{request.criterion_key} at {evidence.graded_sha}: {reason}",
            )

        try:
            repository = self._repository(request.repo_url)
            required = frozenset(
                step.forge_check for step in repository.checks if step.forge_check
            )
            if self._ci is None or self._observations is None:
                raise ValueError("the forge check capabilities are unavailable")
            passed, summary = await self._ci.wait_for_checks(
                repo_url=request.repo_url, ref=evidence.graded_sha
            )
            if passed is None:
                return result(AuditVerdict.UNVERIFIABLE, "no run at the recorded SHA")
            checks = await self._checked_snapshot(request, evidence, passed)
            if passed:
                self._require_roster(checks, required)
                return result(AuditVerdict.HOLDS, "the recorded SHA has green checks")
            names = await self._ci.failed_check_names(
                repo_url=request.repo_url, ref=evidence.graded_sha
            )
            if not names or not names <= checks.check_names:
                raise ValueError("the red check names do not belong to the snapshot")
            red = await classify_red_checks(
                ci=self._ci,
                repository=repository,
                repo_url=repository.url,
                final_commit_sha=evidence.graded_sha,
                initial_summary=summary,
                initial_failed_names=names,
                max_attempts=self._config.delivery_red_rerun_max_attempts,
            )
            if red.checks_passed is None:
                return result(
                    AuditVerdict.UNVERIFIABLE,
                    "the classified rerun has no completed check observation",
                )
            checks = await self._checked_snapshot(request, evidence, red.checks_passed)
            match red.red_class:
                case CheckRedClass.RUNNER_FLAKE:
                    self._require_roster(checks, required)
                    return result(AuditVerdict.HOLDS, "a same-SHA rerun is green")
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
        except Exception as exc:
            return result(AuditVerdict.UNVERIFIABLE, f"{type(exc).__name__}: {exc}")

    async def _checked_snapshot(
        self,
        request: AuditForgeRequest,
        evidence: CriterionEvidence,
        passed: bool,
    ) -> ObservedChecks:
        if self._observations is None:
            raise ValueError("the completed check reader is unavailable")
        checks = await self._observations.observed_checks(
            repo_url=request.repo_url, ref=evidence.graded_sha
        )
        if (
            checks.commit_sha != evidence.graded_sha
            or checks.checks_passed is not passed
        ):
            raise ValueError("the completed observation differs from the requested SHA")
        if not checks.check_names or any(
            not name.strip() for name in checks.check_names
        ):
            raise ValueError("the completed observation has no usable check roster")
        return checks

    @staticmethod
    def _require_roster(checks: ObservedChecks, required: frozenset[str]) -> None:
        if not required <= checks.check_names:
            missing = sorted(required - checks.check_names)
            raise ValueError(f"configured checks were not observed: {missing}")
