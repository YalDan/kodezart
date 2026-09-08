"""Run native scope claim observations without publishing partial audit coverage."""

from dataclasses import dataclass

from kodezart.chains.audit_evidence import AuditEvidenceVerifier
from kodezart.chains.audit_overclaim import AuditOverclaimVerifier
from kodezart.chains.audit_pass import AuditClaimVerifier, AuditMandateHunt
from kodezart.core.config import AppConfig
from kodezart.core.protocols import GitService, RepoCache, TrackerPort
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.audit_requests import (
    AuditRequestReader,
    AuditRequestSnapshot,
    AuditRequestTarget,
)
from kodezart.services.audit_terminal import AuditTerminalReader
from kodezart.services.git_observations import read_remote_head
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimObservation,
    AuditClaimReport,
    AuditClaimRequest,
    AuditMandateRequest,
    AuditVerdict,
)
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.audit_overclaim import (
    AuditOverclaimReport,
    OverclaimReportEntry,
)
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalRequest,
)
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind


@dataclass(frozen=True)
class AuditReadObservation:
    """One retained target and the existing observations actually obtained."""

    target: AuditRequestTarget
    claim: AuditClaimReport | None = None
    evidence: AuditEvidenceObservation | None = None
    terminal: AuditTerminalObservation | None = None
    unavailable_reason: str | None = None
    overclaims: AuditOverclaimReport | None = None
    overclaim_unavailable_reason: str | None = None


@dataclass(frozen=True)
class AuditReadSweepResult:
    """A full native read attempt, never a published or completed audit report.

    This has no coverage-success flag: other detectors, scheduled registration
    and leased publication still need their actual consumers. No invocation
    advances the completed-audit watermark, including one with no read errors.
    """

    sources: AuditRequestSnapshot
    audited_surfaces: tuple[WritableSurface, ...]
    observations: tuple[AuditReadObservation, ...]


def _body_surfaces(snapshot: AuditRequestSnapshot) -> tuple[WritableSurface, ...]:
    """Declare precisely the body set this read attempt can hunt for mandates."""
    keys = {issue.issue_key for issue in snapshot.candidates.issues}
    keys.update(
        target.source.issue.issue_key
        for target in snapshot.targets
        if target.source is not None
    )
    surfaces = [
        WritableSurface(
            kind=SurfaceKind.ISSUE_DESCRIPTION,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
        )
        for key in sorted(keys)
    ]
    if snapshot.candidates.scope.kind is not ScopeKind.ISSUE:
        surfaces.append(
            WritableSurface(
                kind=SurfaceKind.CONTAINER_DESCRIPTION,
                ref=snapshot.candidates.scope,
            )
        )
    return tuple(surfaces)


class AuditReadSweep:
    """One zero-argument, read-only sweep over a constructor-bound native scope.

    Every invocation enumerates every state again. A failed subject cannot
    prevent other independent subjects from being observed. A refuted criterion
    is returned only through the existing mandate-completed report model.
    Terminal refutations retain their native observation and explicitly name
    their still-missing mandate consumer. No timer, scheduler or writer lives
    here, and no partial detector pass enters the audit coverage cache.
    """

    def __init__(
        self,
        *,
        scope: ScopeRef,
        tracker: TrackerPort,
        operation: OperationConfig,
        claims: AuditClaimVerifier,
        evidence: AuditEvidenceVerifier,
        mandates: AuditMandateHunt,
        terminals: AuditTerminalReader,
        git: GitService,
        cache: RepoCache,
        config: AppConfig,
        overclaims: AuditOverclaimVerifier | None = None,
    ) -> None:
        self._scope = scope
        self._requests = AuditRequestReader(tracker=tracker, operation=operation)
        self._review_state = operation.workflow_states.get(LifecycleStage.IN_REVIEW)
        self._claims = claims
        self._evidence = evidence
        self._mandates = mandates
        self._terminals = terminals
        self._git = git
        self._cache = cache
        self._remote = config.git_remote
        self._overclaims = overclaims

    async def _observe(
        self, target: AuditRequestTarget, surfaces: tuple[WritableSurface, ...]
    ) -> AuditReadObservation:
        request = target.request
        if request is None:
            return AuditReadObservation(
                target, unavailable_reason=target.unavailable_reason
            )
        if isinstance(request, AuditTerminalRequest):
            terminal = await self._terminals.observe(request)
            return AuditReadObservation(
                target,
                terminal=terminal,
                unavailable_reason=(
                    "terminal refutation mandate completion is not implemented"
                    if terminal.verdict is AuditVerdict.REFUTED
                    else None
                ),
            )
        evidence = None
        issue = target.issue
        if issue.state_kind is WorkflowStateKind.COMPLETED or (
            issue.state_kind is WorkflowStateKind.STARTED
            and issue.state_name == self._review_state
        ):
            evidence = await self._evidence.observe(request)
            if evidence.is_lapse:
                return AuditReadObservation(target, evidence=evidence)
            claim = evidence.current_claim
            if claim is None:
                raise AuditClaimReadError("current grading has no claim observation")
        else:
            claim = await self._claims.verify(request)
        report = await self._mandates.complete(
            AuditMandateRequest(
                claim=claim,
                defect_class=f"violation of the current Check: {claim.check}",
                surfaces=surfaces,
                repo_url=request.repo_url,
                cache_key=request.cache_key,
            )
        )
        return AuditReadObservation(target, claim=report, evidence=evidence)

    async def _observe_overclaims(
        self, target: AuditRequestTarget, surfaces: tuple[WritableSurface, ...]
    ) -> AuditOverclaimReport:
        request = target.request
        if not isinstance(request, AuditClaimRequest):
            raise AuditClaimReadError(
                "over-claim verification requires a native criterion request"
            )
        if self._overclaims is None:
            raise AuditClaimReadError("the over-claim verifier is not configured")
        observed = await self._overclaims.observe(request)
        reports: list[OverclaimReportEntry] = []
        for reading in observed.judgment.checks:
            reports.append(
                OverclaimReportEntry(
                    kind=reading.kind,
                    report=await self._mandates.complete(
                        AuditMandateRequest(
                            claim=AuditClaimObservation(
                                judgment=AuditClaimJudgment(
                                    criterion_key=observed.judgment.criterion_key,
                                    verdict=reading.verdict,
                                    evidence=reading.evidence,
                                ),
                                head_sha=observed.head_sha,
                                record_ref=observed.record_ref,
                                check=observed.check,
                            ),
                            defect_class=(
                                f"{reading.kind.value} over-claim: {observed.check}"
                            ),
                            surfaces=surfaces,
                            repo_url=request.repo_url,
                            cache_key=request.cache_key,
                        )
                    ),
                )
            )
        return AuditOverclaimReport(observation=observed, reports=tuple(reports))

    async def _require_current(self, observation: AuditReadObservation) -> None:
        target = observation.target
        request = target.request
        if isinstance(request, AuditTerminalRequest):
            if observation.terminal is not None and (
                await self._terminals.observe(request) != observation.terminal
            ):
                raise AuditClaimReadError("terminal changed during the read sweep")
            return
        if not isinstance(request, AuditClaimRequest) or target.source is None:
            return
        heads = set()
        if observation.claim is not None:
            heads.add(observation.claim.claim.head_sha)
        if observation.evidence is not None:
            heads.add(observation.evidence.head_sha)
        if observation.overclaims is not None:
            heads.add(observation.overclaims.observation.head_sha)
        if not heads:
            return
        if len(heads) != 1:
            raise AuditClaimReadError("audit arms observed different branch heads")
        head = next(iter(heads))
        repository = await ensure_repository(
            cache=self._cache, repo_url=request.repo_url, cache_key=request.cache_key
        )
        if (
            await read_remote_head(
                git=self._git,
                repository=repository,
                remote=self._remote,
                branch=target.source.record.branch,
            )
            != head
        ):
            raise AuditClaimReadError("observed branch changed during the read sweep")

    async def run(self) -> AuditReadSweepResult:
        """Read and judge actual scoped inputs, preserving every unavailable entry."""
        snapshot = await self._requests.read(scope=self._scope)
        surfaces = _body_surfaces(snapshot)
        observations: list[AuditReadObservation] = []
        for target in snapshot.targets:
            try:
                observation = await self._observe(target, surfaces)
            except Exception as exc:
                observation = AuditReadObservation(
                    target, unavailable_reason=f"{type(exc).__name__}: {exc}"
                )
            overclaims = None
            overclaim_reason = None
            if "criterion" in target.issue.issue_labels:
                try:
                    overclaims = await self._observe_overclaims(target, surfaces)
                except Exception as exc:
                    overclaim_reason = f"{type(exc).__name__}: {exc}"
            observations.append(
                AuditReadObservation(
                    target=observation.target,
                    claim=observation.claim,
                    evidence=observation.evidence,
                    terminal=observation.terminal,
                    unavailable_reason=observation.unavailable_reason,
                    overclaims=overclaims,
                    overclaim_unavailable_reason=overclaim_reason,
                )
            )
        for observation in observations:
            await self._require_current(observation)
        await self._requests.require_unchanged(snapshot)
        return AuditReadSweepResult(snapshot, surfaces, tuple(observations))
