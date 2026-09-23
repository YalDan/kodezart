"""Run native scope claim observations without publishing partial audit coverage."""

from dataclasses import dataclass

from kodezart.chains.audit_detection_removal import DetectorRemovalVerifier
from kodezart.chains.audit_evidence import AuditEvidenceVerifier, AuditRestampVerifier
from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.chains.audit_overclaim import AuditOverclaimVerifier
from kodezart.chains.audit_pass import AuditClaimVerifier, AuditMandateHunt
from kodezart.core.protocols import GitService, RepoCache, TrackerPort
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.fire_spec import criterion_check, tracker_spec_from_issues
from kodezart.services.audit_failures import AUDIT_READ_FAILURES
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
    AuditMandateContext,
    AuditMandateRequest,
    AuditVerdict,
)
from kodezart.types.domain.audit_detection_removal import (
    DetectorRemovalObservation,
    DetectorRemovalReport,
    DetectorRemovalReportEntry,
)
from kodezart.types.domain.audit_evidence import (
    AuditEvidenceObservation,
    AuditRestampReport,
    AuditRestampTrace,
    restamp_defect_class,
)
from kodezart.types.domain.audit_forge import AuditForgeObservation, AuditForgeRequest
from kodezart.types.domain.audit_overclaim import (
    AuditOverclaimObservation,
    AuditOverclaimReport,
    OverclaimReportEntry,
)
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalReport,
    AuditTerminalRequest,
)
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind

#: Every arm of an observation that carries a verdict, beside the field that
#: completes its refutation with a mandate verdict and the field that says
#: why the hunt could not.  A REFUTED value in an arm with neither is a
#: refutation emitted without its mandate verdict, which the observation
#: refuses to be built as: that refusal is the sweep's own completeness
#: assertion (KOD-516).  The over-claim and detector-removal rows read the
#: raw observation, which is REFUTED when any reading nested in it is.
MANDATED_ARMS: tuple[tuple[str, str, str], ...] = (
    ("terminal", "terminal_report", "unavailable_reason"),
    ("forge", "forge_report", "forge_unavailable_reason"),
    ("restamp", "restamp_report", "unavailable_reason"),
    ("evidence", "claim", "unavailable_reason"),
    ("overclaim_reading", "overclaims", "overclaim_unavailable_reason"),
    ("removal_reading", "detector_removal", "removal_unavailable_reason"),
)


@dataclass(frozen=True)
class AuditReadObservation:
    """One retained target and the existing observations actually obtained.

    Every REFUTED value it carries is completed by its mandate verdict or
    stands beside the reason its hunt could not run; one with neither is
    refused at construction (``MANDATED_ARMS``).
    """

    target: AuditRequestTarget
    claim: AuditClaimReport | None = None
    evidence: AuditEvidenceObservation | None = None
    terminal: AuditTerminalObservation | None = None
    terminal_report: AuditTerminalReport | None = None
    unavailable_reason: str | None = None
    overclaims: AuditOverclaimReport | None = None
    overclaim_unavailable_reason: str | None = None
    detector_removal: DetectorRemovalReport | None = None
    removal_unavailable_reason: str | None = None
    #: The raw over-claim and detector-removal observations, kept whether
    #: or not their mandate hunts completed them into the reports above.
    overclaim_reading: AuditOverclaimObservation | None = None
    removal_reading: DetectorRemovalObservation | None = None
    forge: AuditForgeObservation | None = None
    forge_report: AuditClaimReport | None = None
    forge_unavailable_reason: str | None = None
    restamp: AuditRestampTrace | None = None
    restamp_report: AuditRestampReport | None = None

    def __post_init__(self) -> None:
        for arm, completed, reason in MANDATED_ARMS:
            value = getattr(self, arm)
            if (
                value is not None
                and value.verdict is AuditVerdict.REFUTED
                and getattr(self, completed) is None
                and getattr(self, reason) is None
            ):
                raise ValueError(
                    f"a refutation without its mandate verdict: {arm} requires "
                    "completion or the reason its hunt could not run"
                )
        if (
            self.restamp_report is not None
            and self.restamp_report.trace != self.restamp
        ):
            raise ValueError("restamp report differs from the native trace")
        if (
            self.forge is not None
            and self.forge.verdict is AuditVerdict.REFUTED
            and self.forge_report is not None
            and self.forge_report.mandate is None
        ):
            raise ValueError("forge report differs from the native forge reading")
        if (
            self.claim is not None
            and self.evidence is not None
            and self.evidence.current_claim is not None
            and self.claim.claim != self.evidence.current_claim
        ):
            raise ValueError("claim report differs from the evidence's current claim")
        if (
            self.terminal is not None
            and self.terminal_report is None
            and self.unavailable_reason is None
        ):
            raise ValueError(
                "terminal observation requires completion or unavailability"
            )
        if self.terminal_report is not None:
            if self.terminal_report.observation != self.terminal:
                raise ValueError("terminal report differs from the native observation")
            if self.unavailable_reason is not None:
                raise ValueError(
                    "a complete terminal report cannot also be unavailable"
                )


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
    """One zero-argument observation sweep over a constructor-bound native scope.

    Every invocation enumerates every state again. A failed subject cannot
    prevent other independent subjects from being observed. Every refutation
    the sweep produces runs the same mandate hunt: a criterion claim, each
    over-claim and detector-removal reading, a forge reading at its graded
    sha, a restamp trace at the current head, and a terminal refutation. A
    terminal whose branch no longer exists is emitted as REFUTED with
    ``NO_BRANCH`` and hunted with no head pin, over the tracker surfaces
    alone. A refutation whose hunt fails keeps its raw value beside the
    reason, and never a complete report. No timer, scheduler or writer lives
    here, and no partial detector pass enters the audit coverage cache. The
    forge verifier may request the delivery classifier's bounded same-SHA
    reruns.
    """

    def __init__(
        self,
        *,
        scope: ScopeRef,
        tracker: TrackerPort,
        operation: OperationConfig,
        claims: AuditClaimVerifier,
        evidence: AuditEvidenceVerifier,
        restamps: AuditRestampVerifier,
        mandates: AuditMandateHunt,
        terminals: AuditTerminalReader,
        git: GitService,
        cache: RepoCache,
        remote: str,
        overclaims: AuditOverclaimVerifier | None = None,
        removals: DetectorRemovalVerifier | None = None,
        forge: AuditForgeVerifier | None = None,
    ) -> None:
        self._scope = scope
        self._requests = AuditRequestReader(tracker=tracker, operation=operation)
        self._review_state = operation.workflow_states.get(LifecycleStage.IN_REVIEW)
        self._claims = claims
        self._evidence = evidence
        self._restamps = restamps
        self._mandates = mandates
        self._terminals = terminals
        self._git = git
        self._cache = cache
        self._remote = remote
        self._overclaims = overclaims
        self._removals = removals
        self._forge = forge

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
            try:
                mandate = None
                if terminal.verdict is AuditVerdict.REFUTED:
                    # A missing branch has no head: the hunt runs unpinned.
                    mandate = await self._mandates.observe(
                        AuditMandateContext(
                            defect_class=terminal.defect_class(),
                            refutation_evidence=terminal.refutation_evidence(),
                            head_sha=terminal.branch_head,
                            surfaces=surfaces,
                            repo_url=request.repo_url,
                            cache_key=request.cache_key,
                        )
                    )
                terminal_report = AuditTerminalReport(
                    observation=terminal, mandate=mandate
                )
            except AUDIT_READ_FAILURES as exc:
                return AuditReadObservation(
                    target,
                    terminal=terminal,
                    unavailable_reason=f"{type(exc).__name__}: {exc}",
                )
            return AuditReadObservation(
                target, terminal=terminal, terminal_report=terminal_report
            )
        evidence = None
        restamp = None
        restamp_report = None
        issue = target.issue
        if issue.state_kind is WorkflowStateKind.COMPLETED or (
            issue.state_kind is WorkflowStateKind.STARTED
            and issue.state_name == self._review_state
        ):
            evidence = await self._evidence.observe(request)
            restamp = await self._restamps.observe(
                request=request, evidence=evidence.recorded_evidence
            )
            # Completed before the lapse return: a lapse is exactly a row
            # whose commit is behind head, so it is the case the trace is
            # most about, and its refutation carries its mandate verdict too.
            try:
                restamp_report = await self._restamp_report(
                    trace=restamp,
                    head_sha=evidence.head_sha,
                    surfaces=surfaces,
                    request=request,
                )
            except AUDIT_READ_FAILURES as exc:
                return AuditReadObservation(
                    target,
                    evidence=evidence,
                    restamp=restamp,
                    unavailable_reason=f"{type(exc).__name__}: {exc}",
                )
            if evidence.is_lapse:
                return AuditReadObservation(
                    target,
                    evidence=evidence,
                    restamp=restamp,
                    restamp_report=restamp_report,
                )
            claim = evidence.current_claim
            if claim is None:
                raise AuditClaimReadError("current grading has no claim observation")
        else:
            # No Evidence row was read, so there is no restamp to trace.
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
        return AuditReadObservation(
            target,
            claim=report,
            evidence=evidence,
            restamp=restamp,
            restamp_report=restamp_report,
        )

    async def _restamp_report(
        self,
        *,
        trace: AuditRestampTrace | None,
        head_sha: str,
        surfaces: tuple[WritableSurface, ...],
        request: AuditClaimRequest,
    ) -> AuditRestampReport | None:
        """Complete a refuted restamp trace with the mandate hunt at *head_sha*."""
        if trace is None:
            return None
        mandate = None
        if trace.verdict is AuditVerdict.REFUTED:
            mandate = await self._mandates.observe(
                AuditMandateContext(
                    defect_class=restamp_defect_class(trace),
                    refutation_evidence=trace.reason,
                    head_sha=head_sha,
                    surfaces=surfaces,
                    repo_url=request.repo_url,
                    cache_key=request.cache_key,
                )
            )
        return AuditRestampReport(trace=trace, mandate=mandate)

    async def _observe_overclaims(
        self, target: AuditRequestTarget, surfaces: tuple[WritableSurface, ...]
    ) -> tuple[AuditOverclaimObservation, AuditOverclaimReport | None, str | None]:
        request = target.request
        if not isinstance(request, AuditClaimRequest):
            raise AuditClaimReadError(
                "over-claim verification requires a native criterion request"
            )
        if self._overclaims is None:
            raise AuditClaimReadError("the over-claim verifier is not configured")
        observed = await self._overclaims.observe(request)
        try:
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
            report = AuditOverclaimReport(observation=observed, reports=tuple(reports))
        except AUDIT_READ_FAILURES as exc:
            # The raw readings stay beside the reason: a refutation whose
            # hunt failed is kept, never dropped with the report.
            return observed, None, f"{type(exc).__name__}: {exc}"
        return observed, report, None

    async def _observe_removals(
        self, target: AuditRequestTarget, surfaces: tuple[WritableSurface, ...]
    ) -> tuple[DetectorRemovalObservation, DetectorRemovalReport | None, str | None]:
        request = target.request
        if not isinstance(request, AuditClaimRequest):
            raise AuditClaimReadError(
                "detector-removal verification requires a native criterion request"
            )
        if self._removals is None:
            raise AuditClaimReadError("the detector-removal verifier is not configured")
        observed = await self._removals.observe(request)
        try:
            reports = []
            for finding in observed.judgment.findings or (None,):
                report = await self._mandates.complete(
                    AuditMandateRequest(
                        claim=observed.claim(finding),
                        defect_class=observed.defect_class(finding),
                        surfaces=surfaces,
                        repo_url=request.repo_url,
                        cache_key=request.cache_key,
                    )
                )
                reports.append(
                    DetectorRemovalReportEntry(finding=finding, report=report)
                )
            completed = DetectorRemovalReport(
                observation=observed, reports=tuple(reports)
            )
        except AUDIT_READ_FAILURES as exc:
            # The raw finding stays beside the reason, as the over-claim's does.
            return observed, None, f"{type(exc).__name__}: {exc}"
        return observed, completed, None

    async def _observe_forge(
        self, target: AuditRequestTarget, surfaces: tuple[WritableSurface, ...]
    ) -> tuple[AuditForgeObservation, AuditClaimReport | None, str | None]:
        request = target.request
        if not isinstance(request, AuditClaimRequest) or target.source is None:
            raise AuditClaimReadError(
                "forge verification requires a native criterion request and record"
            )
        if target.issue.state_kind is not WorkflowStateKind.COMPLETED:
            raise AuditClaimReadError(
                "forge verification requires a completed criterion"
            )
        if self._forge is None:
            raise AuditClaimReadError("the forge verifier is not configured")
        observed = await self._forge.observe(
            AuditForgeRequest(
                criterion_key=tracker_spec_from_issues(
                    subject=target.source.issue, criteria=(target.issue,)
                ).criteria[0],
                lane_issue_key=request.lane_issue_key,
                repo_url=request.repo_url,
            )
        )
        if observed.criterion != target.issue:
            raise AuditClaimReadError(
                "the forge criterion differs from the collected target"
            )
        try:
            check = criterion_check(
                criterion=observed.criterion, issue_key=request.lane_issue_key
            )
            claim = AuditClaimObservation(
                judgment=AuditClaimJudgment(
                    criterion_key=observed.criterion.issue_key,
                    verdict=observed.verdict,
                    evidence=observed.reason,
                ),
                head_sha=observed.recorded_evidence.graded_sha,
                record_ref=target.source.comment.comment_key,
                check=check,
            )
            report = await self._mandates.complete(
                AuditMandateRequest(
                    claim=claim,
                    defect_class=f"forge checks refute the recorded grading: {check}",
                    surfaces=surfaces,
                    repo_url=request.repo_url,
                    cache_key=request.cache_key,
                )
            )
            if report.claim != claim:
                raise AuditClaimReadError("the forge report changed the observed claim")
            return observed, report, None
        except AUDIT_READ_FAILURES as exc:
            return observed, None, f"{type(exc).__name__}: {exc}"

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
        if observation.detector_removal is not None:
            heads.add(observation.detector_removal.observation.head_sha)
        # Forge checks grade a historical Evidence SHA. Their mandate report
        # retains that SHA and cannot participate in current-head equality.
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

    async def prepare(self) -> AuditRequestSnapshot:
        """Read the complete current native candidate and source snapshot."""
        return await self._requests.read(scope=self._scope)

    async def require_current(
        self,
        snapshot: AuditRequestSnapshot,
        observations: tuple[AuditReadObservation, ...],
    ) -> None:
        """Refuse head/source drift before publication or coverage completion."""
        for observation in observations:
            await self._require_current(observation)
        await self._requests.require_unchanged(snapshot)

    async def observe_target(
        self, *, snapshot: AuditRequestSnapshot, target: AuditRequestTarget
    ) -> AuditReadObservation:
        """Observe one selected native target with every existing applicable arm."""
        if target not in snapshot.targets:
            raise AuditClaimReadError("selected audit target is outside its snapshot")
        surfaces = _body_surfaces(snapshot)
        try:
            observation = await self._observe(target, surfaces)
        except AUDIT_READ_FAILURES as exc:
            observation = AuditReadObservation(
                target, unavailable_reason=f"{type(exc).__name__}: {exc}"
            )
        overclaim_reading = None
        overclaims = None
        overclaim_reason = None
        removal_reading = None
        removals = None
        removal_reason = None
        forge = None
        forge_report = None
        forge_reason = None
        if "criterion" in target.issue.issue_labels:
            try:
                (
                    overclaim_reading,
                    overclaims,
                    overclaim_reason,
                ) = await self._observe_overclaims(target, surfaces)
            except AUDIT_READ_FAILURES as exc:
                overclaim_reason = f"{type(exc).__name__}: {exc}"
            try:
                (
                    removal_reading,
                    removals,
                    removal_reason,
                ) = await self._observe_removals(target, surfaces)
            except AUDIT_READ_FAILURES as exc:
                removal_reason = f"{type(exc).__name__}: {exc}"
            try:
                forge, forge_report, forge_reason = await self._observe_forge(
                    target, surfaces
                )
            except AUDIT_READ_FAILURES as exc:
                forge_reason = f"{type(exc).__name__}: {exc}"
        return AuditReadObservation(
            target=observation.target,
            claim=observation.claim,
            evidence=observation.evidence,
            terminal=observation.terminal,
            terminal_report=observation.terminal_report,
            unavailable_reason=observation.unavailable_reason,
            overclaims=overclaims,
            overclaim_unavailable_reason=overclaim_reason,
            detector_removal=removals,
            removal_unavailable_reason=removal_reason,
            overclaim_reading=overclaim_reading,
            removal_reading=removal_reading,
            forge=forge,
            forge_report=forge_report,
            forge_unavailable_reason=forge_reason,
            # This method rebuilds the observation field by field, so a field
            # not listed here is dropped before anything composed sees it.
            restamp=observation.restamp,
            restamp_report=observation.restamp_report,
        )

    async def run(self) -> AuditReadSweepResult:
        """Read every current target without claiming publication or coverage."""
        snapshot = await self.prepare()
        observations = tuple(
            [
                await self.observe_target(snapshot=snapshot, target=target)
                for target in snapshot.targets
            ]
        )
        await self.require_current(snapshot, observations)
        return AuditReadSweepResult(snapshot, _body_surfaces(snapshot), observations)
