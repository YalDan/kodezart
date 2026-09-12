"""One scheduler-driven audit across explicitly configured native scopes."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from kodezart.chains.audit_sweep import AuditReadObservation, AuditReadSweep
from kodezart.core.logging import get_logger
from kodezart.core.protocols import GitService, RepoCache, TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.audit_coverage import AuditCoverage
from kodezart.services.audit_escalation import AuditEscalations
from kodezart.services.audit_failures import AUDIT_PUBLICATION_FAILURES
from kodezart.services.audit_publication import AuditPublisher
from kodezart.services.audit_requests import AuditRequestSnapshot
from kodezart.services.git_observations import read_remote_head
from kodezart.services.repo_observations import ensure_repository
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.audit import (
    AuditCandidate,
    AuditCoverageResult,
    AuditVerdict,
    InstructedMandateObservation,
)
from kodezart.types.domain.audit_detection_removal import DetectorRemovalObservation
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.audit_forge import AuditForgeObservation
from kodezart.types.domain.audit_overclaim import AuditOverclaimObservation
from kodezart.types.domain.audit_runtime import (
    AuditClaimPublication,
    AuditForgePublication,
    AuditOverclaimPublication,
    AuditPublication,
    AuditPublishedArtifact,
    AuditRemovalPublication,
    AuditRepairInput,
    AuditRunReport,
    AuditScopeComplete,
    AuditScopeIncomplete,
    AuditScopeReport,
    AuditScopeSummary,
    AuditTerminalPublication,
    AuditUnavailable,
)
from kodezart.types.domain.audit_terminal import AuditTerminalObservation
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import (
    AuditScopeBinding,
    OperationConfig,
    RepoEntry,
    RunKind,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult

_INCOMPLETE = AUDIT_PUBLICATION_FAILURES


class AuditRunIncompleteError(Exception):
    """The scheduled audit retained its receipts without claiming full coverage."""

    def __init__(self, *, report: AuditRunReport) -> None:
        self.report = report
        super().__init__(f"audit run {report.identity.title()!r} is incomplete")


@dataclass
class _AuditAttempt:
    snapshot: AuditRequestSnapshot
    observations: list[AuditReadObservation]
    repair_observations: list[AuditReadObservation]


@dataclass(frozen=True)
class AuditTarget:
    binding: AuditScopeBinding
    repository: RepoEntry
    sweep: AuditReadSweep
    publisher: AuditPublisher
    escalations: AuditEscalations


def _accept_own_write(
    before: AuditRequestSnapshot,
    current: AuditRequestSnapshot,
    issue_key: str,
    *,
    classification: bool,
) -> AuditRequestSnapshot:
    """Permit the known comment timestamp or decision label; other facts stay pinned."""
    current_issues = {issue.issue_key: issue for issue in current.candidates.issues}
    current_issues.update(
        (item.source.issue.issue_key, item.source.issue)
        for item in current.targets
        if item.source is not None
    )

    def expected(issue: TrackerIssue) -> TrackerIssue:
        if issue.issue_key != issue_key:
            return issue
        if issue_key not in current_issues:
            raise AuditClaimReadError("the written audit subject left its source set")
        observed = current_issues[issue_key]
        return TrackerIssue.model_validate(
            {
                **issue.model_dump(),
                "issue_labels": issue.issue_labels | {"decision"}
                if classification
                else issue.issue_labels,
                "updated_at": observed.updated_at,
            }
        )

    wanted = AuditRequestSnapshot(
        candidates=replace(
            before.candidates,
            issues=tuple(expected(issue) for issue in before.candidates.issues),
        ),
        targets=tuple(
            replace(
                item,
                issue=expected(item.issue),
                source=None
                if item.source is None
                else replace(item.source, issue=expected(item.source.issue)),
            )
            for item in before.targets
        ),
    )
    if current != wanted:
        raise AuditClaimReadError(
            "audit sources changed outside the known comment or classification write"
        )
    return current


def _reports(observation: AuditReadObservation) -> tuple[AuditPublication, ...]:
    found: list[AuditPublication] = []
    if observation.claim is not None:
        found.append(
            AuditClaimPublication(detector="current_check", report=observation.claim)
        )
    if observation.forge_report is not None:
        if observation.forge is None:
            raise AuditClaimReadError("a forge report lacks its historical observation")
        found.append(
            AuditForgePublication(
                graded_sha=observation.forge.recorded_evidence.graded_sha,
                report=observation.forge_report,
            )
        )
    if observation.overclaims is not None:
        found.extend(
            AuditOverclaimPublication(entry=entry)
            for entry in observation.overclaims.reports
        )
    if observation.detector_removal is not None:
        found.extend(
            AuditRemovalPublication(entry=entry)
            for entry in observation.detector_removal.reports
        )
    if observation.terminal_report is not None:
        found.append(AuditTerminalPublication(report=observation.terminal_report))
    return tuple(found)


def _raw_observations(
    observations: Sequence[AuditReadObservation],
) -> tuple[
    AuditEvidenceObservation
    | AuditForgeObservation
    | AuditTerminalObservation
    | AuditOverclaimObservation
    | DetectorRemovalObservation,
    ...,
]:
    return tuple(
        item
        for observation in observations
        for item in (
            observation.evidence,
            observation.forge,
            observation.terminal,
            None
            if observation.overclaims is None
            else observation.overclaims.observation,
            None
            if observation.detector_removal is None
            else observation.detector_removal.observation,
        )
        if item is not None
    )


def _all_reports(
    observations: Sequence[AuditReadObservation],
) -> tuple[AuditPublication, ...]:
    return tuple(item for observation in observations for item in _reports(observation))


def _head(publication: AuditPublication) -> str:
    if not isinstance(publication, AuditTerminalPublication):
        return publication.report.claim.head_sha
    head = publication.report.observation.branch_head
    if head is None:
        raise AuditClaimReadError(
            "the terminal report has no immutable verification head"
        )
    return head


def _detector(publication: AuditPublication) -> str:
    if isinstance(publication, AuditClaimPublication):
        return publication.detector
    if isinstance(publication, AuditForgePublication):
        return "forge"
    if isinstance(publication, AuditOverclaimPublication):
        return f"overclaim:{publication.entry.kind.value}"
    if isinstance(publication, AuditRemovalPublication):
        finding = publication.entry.finding
        if finding is None:
            return "detector_removal:none"
        addresses = [
            finding.mechanism.path,
            finding.mechanism.line,
            finding.detector.path,
            finding.detector.line,
        ]
        return (
            "detector_removal:"
            + hashlib.sha256(json.dumps(addresses).encode()).hexdigest()
        )
    return "terminal"


def _require_payload(result: WriteBackResult, body: str) -> None:
    surface = result.artifact.surface
    if surface.kind is not SurfaceKind.MARKER_COMMENT or surface.marker is None:
        raise AuditClaimReadError("audit publication read back another surface kind")
    if result.artifact.content != marked_comment_body(marker=surface.marker, body=body):
        raise AuditClaimReadError(
            "the read-back audit artifact changed its source identities"
        )


class AuditScheduledPass:
    """No clock, state applier or alternate verification loop lives here."""

    def __init__(
        self,
        *,
        targets: Sequence[AuditTarget],
        coverage: AuditCoverage,
        tracker: TrackerPort,
        operation: OperationConfig,
        git: GitService,
        cache: RepoCache,
        remote: str,
    ) -> None:
        self._targets, self._coverage = tuple(targets), coverage
        self._tracker, self._operation = tracker, operation
        self._git, self._cache, self._remote = git, cache, remote
        self._log = get_logger(__name__)
        self._last_report: AuditRunReport | None = None

    @property
    def last_report(self) -> AuditRunReport | None:
        """Actual last invocation evidence, never a durable coverage authority."""
        return self._last_report

    async def _scope(
        self, target: AuditTarget, identity: RunIdentity
    ) -> AuditScopeReport:
        writes: list[WriteBackResult] = []
        interrupted: list[AuditRepairInput] = []
        unavailable: list[AuditUnavailable] = []
        observations: list[AuditReadObservation] = []
        repair_observations: list[AuditReadObservation] = []
        scope = target.binding.scope

        def refuse(subject: ScopeRef, reason: str) -> None:
            unavailable.append(AuditUnavailable(subject=subject, reason=reason))

        try:
            snapshot = await target.sweep.prepare()
            if any(
                item.request is not None
                and item.request.repo_url != target.repository.url
                for item in snapshot.targets
            ):
                raise AuditClaimReadError(
                    "a native audit source resolves outside the configured "
                    "repository binding"
                )
            context = _AuditAttempt(snapshot, observations, repair_observations)
            by_key = {item.issue.issue_key: item for item in snapshot.targets}

            async def visit(candidate: AuditCandidate) -> None:
                observations.append(
                    await target.sweep.observe_target(
                        snapshot=snapshot, target=by_key[candidate.issue_key]
                    )
                )

            async def require_current() -> None:
                await target.sweep.require_current(
                    context.snapshot,
                    (*observations, *context.repair_observations),
                )

            async def complete(coverage: AuditCoverageResult) -> None:
                await require_current()
                for observation in observations:
                    subject = ScopeRef(
                        kind=ScopeKind.ISSUE, key=observation.target.issue.issue_key
                    )
                    reasons = (
                        observation.unavailable_reason,
                        observation.overclaim_unavailable_reason,
                        observation.removal_unavailable_reason,
                        observation.forge_unavailable_reason,
                    )
                    for reason in reasons:
                        if reason is not None:
                            refuse(subject, reason)
                    if (
                        observation.evidence is not None
                        and observation.evidence.is_lapse
                    ):
                        refuse(
                            subject,
                            "native lapse classification and its state authority "
                            "are unavailable",
                        )
                        continue
                    for publication in _reports(observation):
                        try:
                            await self._publish(
                                target=target,
                                context=context,
                                observation=observation,
                                publication=publication,
                                identity=identity,
                                writes=writes,
                                interrupted=interrupted,
                            )
                        except _INCOMPLETE as exc:
                            refuse(subject, f"{type(exc).__name__}: {exc}")
                if unavailable:
                    raise AuditRunIncompleteError(
                        report=AuditRunReport(
                            identity=identity,
                            scopes=(
                                AuditScopeIncomplete(
                                    scope=scope,
                                    writes=tuple(writes),
                                    unavailable=tuple(unavailable),
                                    repair_inputs=tuple(interrupted),
                                    observations=_all_reports(
                                        (*observations, *repair_observations)
                                    ),
                                    raw_observations=_raw_observations(
                                        (*observations, *repair_observations)
                                    ),
                                ),
                            ),
                        )
                    )
                await require_current()
                repository = await ensure_repository(
                    cache=self._cache, repo_url=target.repository.url, cache_key=None
                )
                head = await read_remote_head(
                    git=self._git,
                    repository=repository,
                    remote=self._remote,
                    branch=target.repository.trunk,
                )
                if head is None:
                    raise AuditClaimReadError(
                        "the audit summary repository has no remote trunk head"
                    )
                destination = await self._tracker.read_issue(
                    issue_key=target.binding.report_issue_key
                )
                if destination.issue_key != target.binding.report_issue_key:
                    raise AuditClaimReadError(
                        "the audit report destination returned another identity"
                    )
                summary = AuditScopeSummary(
                    identity=identity,
                    coverage=coverage,
                    record_refs=tuple(result.artifact.native_ref for result in writes),
                    records=tuple(result.artifact for result in writes),
                )

                async def require_summary_sources() -> None:
                    await require_current()
                    for expected in summary.records:
                        current = await read_tracker_artifact(
                            tracker=self._tracker, surface=expected.surface
                        )
                        if current != expected:
                            raise AuditClaimReadError(
                                "a previously verified audit record changed before "
                                "summary publication"
                            )

                async def summary_body(_finding: WriteBackFinding | None) -> str:
                    await require_summary_sources()
                    return summary.model_dump_json()

                async def accept_summary() -> None:
                    context.snapshot = _accept_own_write(
                        context.snapshot,
                        await target.sweep.prepare(),
                        destination.issue_key,
                        classification=False,
                    )

                summary_result = await target.publisher.publish(
                    issue_key=destination.issue_key,
                    marker=compose_comment_marker(
                        prefixes=self._operation.marker_prefixes,
                        purpose="audit",
                        lane=f"{scope.kind.value}:{scope.key}",
                        occurrence_key=identity.title(),
                    ),
                    ref=head,
                    job_id=identity.title(),
                    visibility=self._operation.board_visibility(destination.team_key),
                    compose=summary_body,
                    require_current=require_summary_sources,
                    accept_write=accept_summary,
                    interrupted=interrupted,
                )
                writes.append(summary_result)
                await accept_summary()
                _require_payload(summary_result, summary.model_dump_json())
                if summary_result.verdict is not AuditVerdict.HOLDS:
                    raise AuditClaimReadError(
                        "the audit summary exhausted canonical write verification"
                    )
                await require_summary_sources()

            coverage = await self._coverage.cover(
                scope=scope,
                candidates=snapshot.candidates.candidates,
                observed_at=identity.started_at,
                visit=visit,
                complete=complete,
            )
            return AuditScopeComplete(
                scope=scope,
                coverage=coverage,
                writes=tuple(writes),
                observations=_all_reports((*observations, *repair_observations)),
                raw_observations=_raw_observations(
                    (*observations, *repair_observations)
                ),
            )
        except AuditRunIncompleteError as exc:
            return exc.report.scopes[0]
        except _INCOMPLETE as exc:
            refuse(scope, f"{type(exc).__name__}: {exc}")
            return AuditScopeIncomplete(
                scope=scope,
                writes=tuple(writes),
                unavailable=tuple(unavailable),
                repair_inputs=tuple(interrupted),
                observations=_all_reports((*observations, *repair_observations)),
                raw_observations=_raw_observations(
                    (*observations, *repair_observations)
                ),
            )

    async def _publish(
        self,
        *,
        target: AuditTarget,
        context: _AuditAttempt,
        observation: AuditReadObservation,
        publication: AuditPublication,
        identity: RunIdentity,
        writes: list[WriteBackResult],
        interrupted: list[AuditRepairInput],
    ) -> None:
        observations = context.observations
        verdict = (
            publication.report.claim.judgment.verdict
            if not isinstance(publication, AuditTerminalPublication)
            else publication.report.observation.verdict
        )
        if verdict is AuditVerdict.UNVERIFIABLE:
            raise AuditClaimReadError(
                "an unverifiable claim supplies no authorized tracker write"
            )
        request = observation.target.request
        if request is None:
            raise AuditClaimReadError(
                "publication has no current native source request"
            )
        detector = _detector(publication)
        current = publication
        escalation_refs: dict[str, str] = {}

        async def require_current() -> None:
            await target.sweep.require_current(
                context.snapshot, (*observations, *context.repair_observations)
            )

        async def accept_classification() -> None:
            context.snapshot = _accept_own_write(
                context.snapshot,
                await target.sweep.prepare(),
                observation.target.issue.issue_key,
                classification=True,
            )

        async def accept_comment() -> None:
            context.snapshot = _accept_own_write(
                context.snapshot,
                await target.sweep.prepare(),
                observation.target.issue.issue_key,
                classification=False,
            )

        async def compose(finding: WriteBackFinding | None) -> str:
            nonlocal current
            if finding is not None:
                refreshed = await target.sweep.observe_target(
                    snapshot=context.snapshot,
                    target=next(
                        item
                        for item in context.snapshot.targets
                        if item.issue.issue_key == observation.target.issue.issue_key
                    ),
                )
                context.repair_observations.append(refreshed)
                matches = [
                    item for item in _reports(refreshed) if _detector(item) == detector
                ]
                if len(matches) != 1:
                    raise AuditClaimReadError(
                        "repair did not reproduce the exact report identity"
                    )
                current = matches[0]
                if _head(current) != _head(publication):
                    raise AuditClaimReadError(
                        "repair changed the report's immutable source head"
                    )
            current_verdict = (
                current.report.claim.judgment.verdict
                if not isinstance(current, AuditTerminalPublication)
                else current.report.observation.verdict
            )
            if current_verdict is AuditVerdict.UNVERIFIABLE:
                raise AuditClaimReadError(
                    "fresh report repair became unverifiable before publication"
                )
            mandate = current.report.mandate
            if mandate is not None and isinstance(
                mandate.root, InstructedMandateObservation
            ):
                key = mandate.root.finding.model_dump_json()
                if key not in escalation_refs:
                    escalation_refs[key] = await target.escalations.raise_mandate(
                        issue_key=observation.target.issue.issue_key,
                        lane_key=request.lane_key,
                        job_id=identity.title(),
                        head_sha=_head(current),
                        mandate=mandate.root,
                        visibility=self._operation.board_visibility(
                            observation.target.issue.team_key
                        ),
                        publisher=target.publisher,
                        require_current=require_current,
                        accept_classification=accept_classification,
                        accept_comment=accept_comment,
                        writes=writes,
                        interrupted=interrupted,
                    )
            return AuditPublishedArtifact(
                publication=current, escalation_refs=tuple(escalation_refs.values())
            ).model_dump_json()

        result = await target.publisher.publish(
            issue_key=observation.target.issue.issue_key,
            marker=compose_comment_marker(
                prefixes=self._operation.marker_prefixes,
                purpose="audit",
                lane=request.lane_key,
                occurrence_key=f"{identity.title()}:{observation.target.issue.issue_key}:{detector}",
            ),
            ref=_head(publication),
            job_id=identity.title(),
            visibility=self._operation.board_visibility(
                observation.target.issue.team_key
            ),
            compose=compose,
            require_current=require_current,
            accept_write=accept_comment,
            interrupted=interrupted,
        )
        writes.append(result)
        expected_body = AuditPublishedArtifact(
            publication=current, escalation_refs=tuple(escalation_refs.values())
        ).model_dump_json()
        await accept_comment()
        _require_payload(result, expected_body)
        await require_current()
        if result.verdict is not AuditVerdict.HOLDS:
            raise AuditClaimReadError(
                "the audit artifact exhausted canonical write verification"
            )
        final_verdict = (
            current.report.claim.judgment.verdict
            if not isinstance(current, AuditTerminalPublication)
            else current.report.observation.verdict
        )
        if final_verdict is AuditVerdict.REFUTED:
            raise AuditClaimReadError(
                "the refutation is published; its workflow-state authority "
                "remains unresolved"
            )

    async def run(self, started_at: datetime) -> PassRun:
        identity = RunIdentity(kind=RunKind.AUDIT, name="audit", started_at=started_at)
        self._last_report = None
        scopes = tuple(
            [await self._scope(target, identity) for target in self._targets]
        )
        report = AuditRunReport(identity=identity, scopes=scopes)
        self._last_report = report
        await self._log.ainfo(
            "audit_run_finished",
            run_identity=identity.title(),
            report=report.model_dump(mode="json"),
        )
        if any(isinstance(scope, AuditScopeIncomplete) for scope in scopes):
            raise AuditRunIncompleteError(report=report)
        return PassRun.RAN if scopes else PassRun.SKIPPED
