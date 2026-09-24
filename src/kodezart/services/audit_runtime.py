"""One scheduler-driven audit across explicitly configured native scopes."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial

from kodezart.chains.audit_sweep import AuditReadObservation, AuditReadSweep
from kodezart.core.logging import get_logger
from kodezart.core.protocols import GitService, RepoCache, TrackerPort
from kodezart.domain.audit_claims import (
    audit_deferral,
    mandate_escalation_key,
    reopens_criterion,
)
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.audit_coverage import AuditCoverage
from kodezart.services.audit_escalation import AuditEscalations
from kodezart.services.audit_expectation import expected_after_audit_write
from kodezart.services.audit_failures import AUDIT_PUBLICATION_FAILURES
from kodezart.services.audit_publication import AuditPublisher
from kodezart.services.audit_reopen import AuditReopener
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
from kodezart.types.domain.audit_evidence import (
    AuditEvidenceObservation,
    AuditRestampTrace,
)
from kodezart.types.domain.audit_forge import AuditForgeObservation
from kodezart.types.domain.audit_overclaim import AuditOverclaimObservation
from kodezart.types.domain.audit_runtime import (
    AuditClaimPublication,
    AuditDeferral,
    AuditDeferred,
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
    LifecycleStage,
    OperationConfig,
    OrganizeScopeBinding,
    RepoEntry,
    RunKind,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult

_INCOMPLETE = AUDIT_PUBLICATION_FAILURES


class AuditRunIncompleteError(Exception):
    """The scheduled audit retained its receipts without claiming full coverage."""

    def __init__(self, *, report: AuditRunReport) -> None:
        self.report = report
        super().__init__(f"audit run {report.identity.title()!r} is incomplete")


@dataclass
class _AuditAttempt:
    """Everything one scope attempt accumulates, so its phases share one object."""

    snapshot: AuditRequestSnapshot
    observations: list[AuditReadObservation]
    repair_observations: list[AuditReadObservation]
    writes: list[WriteBackResult]
    interrupted: list[AuditRepairInput]
    unavailable: list[AuditUnavailable]
    deferred: list[AuditDeferred]


@dataclass(frozen=True)
class AuditTarget:
    """One audited scope, with its report destination already resolved.

    The destination is a plain string here rather than the row's optional
    member: a row that declares none stops the audit's composition by name,
    so a target that exists has one and nothing downstream re-asks.
    """

    binding: OrganizeScopeBinding
    report_issue_key: str
    repository: RepoEntry
    sweep: AuditReadSweep
    publisher: AuditPublisher
    escalations: AuditEscalations
    reopener: AuditReopener


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

    expected = partial(
        expected_after_audit_write,
        issue_key=issue_key,
        current_issues=current_issues,
        classification=classification,
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
    | AuditRestampTrace
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
            observation.restamp,
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
    """No clock and no alternate verification loop lives here.

    The one state move is the reopener's, inside the publisher's leased
    write-back, and it is a scope's last act.
    """

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
        # The same source the read sweep reads the review state from, so a
        # deferral and the sweep's own arms cannot disagree about it.
        self._review_state = operation.workflow_states.get(LifecycleStage.IN_REVIEW)
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
        deferred: list[AuditDeferred] = []
        observations: list[AuditReadObservation] = []
        repair_observations: list[AuditReadObservation] = []
        scope = target.binding.scope

        def refuse(subject: ScopeRef, reason: str) -> None:
            unavailable.append(AuditUnavailable(subject=subject, reason=reason))

        def defer(subject: ScopeRef, reason: AuditDeferral) -> None:
            deferred.append(AuditDeferred(subject=subject, reason=reason))

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
            context = _AuditAttempt(
                snapshot,
                observations,
                repair_observations,
                writes,
                interrupted,
                unavailable,
                deferred,
            )
            by_key = {item.issue.issue_key: item for item in snapshot.targets}

            async def visit(candidate: AuditCandidate) -> None:
                item = by_key[candidate.issue_key]
                reason = audit_deferral(
                    issue=item.issue, review_state=self._review_state
                )
                if reason is not None:
                    # Nothing to audit yet, decided from the member's own
                    # state: no session, no git read and no forge read is
                    # spent on it, and no write addresses it.
                    defer(
                        ScopeRef(kind=ScopeKind.ISSUE, key=item.issue.issue_key), reason
                    )
                    return
                observations.append(
                    await target.sweep.observe_target(snapshot=snapshot, target=item)
                )

            async def require_current() -> None:
                await target.sweep.require_current(
                    context.snapshot,
                    (*observations, *context.repair_observations),
                )

            async def complete(coverage: AuditCoverageResult) -> None:
                owed: list[tuple[AuditReadObservation, AuditPublication, str]] = []
                await require_current()
                for observation in observations:
                    subject = ScopeRef(
                        kind=ScopeKind.ISSUE, key=observation.target.issue.issue_key
                    )
                    if (
                        observation.evidence is not None
                        and observation.evidence.is_lapse
                    ):
                        # A grading behind the head is the member's own state
                        # saying its claim was made about another commit. It
                        # is decided before the side arms are read, because
                        # their readings about it are discarded either way.
                        defer(subject, AuditDeferral.GRADED_BEHIND_HEAD)
                        continue
                    reasons = (
                        observation.unavailable_reason,
                        observation.overclaim_unavailable_reason,
                        observation.removal_unavailable_reason,
                        observation.forge_unavailable_reason,
                    )
                    for reason in reasons:
                        if reason is not None:
                            refuse(subject, reason)
                    for publication in _reports(observation):
                        try:
                            current = await self._publish(
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
                            continue
                        if reopens_criterion(current):
                            # The publication's own verified comment is the
                            # last write it appended, and it is the evidence
                            # this criterion's reopen stands on.
                            owed.append(
                                (observation, current, writes[-1].artifact.native_ref)
                            )
                if not unavailable:
                    try:
                        await self._summarize(
                            target=target,
                            context=context,
                            identity=identity,
                            coverage=coverage,
                            deferred=deferred,
                            writes=writes,
                            interrupted=interrupted,
                        )
                    except _INCOMPLETE as exc:
                        # The report is the last reader of this tick, not its
                        # first writer. A report step that cannot complete
                        # refuses the scope's coverage and leaves everything
                        # already recorded where it is.
                        refuse(scope, f"{type(exc).__name__}: {exc}")
                if owed:
                    # Every earlier step re-reads the whole snapshot and
                    # refuses any change but a comment stamp or the decision
                    # label, so the state moves come last, once, as a batch.
                    try:
                        await require_current()
                    except _INCOMPLETE as exc:
                        refuse(scope, f"{type(exc).__name__}: {exc}")
                        owed = []
                    for observation, publication, refutation_ref in owed:
                        subject = ScopeRef(
                            kind=ScopeKind.ISSUE,
                            key=observation.target.issue.issue_key,
                        )
                        try:
                            await self._take_back(
                                target=target,
                                context=context,
                                identity=identity,
                                observation=observation,
                                publication=publication,
                                refutation_ref=refutation_ref,
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
                                    deferred=tuple(deferred),
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
                deferred=tuple(deferred),
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
                deferred=tuple(deferred),
                repair_inputs=tuple(interrupted),
                observations=_all_reports((*observations, *repair_observations)),
                raw_observations=_raw_observations(
                    (*observations, *repair_observations)
                ),
            )

    async def _take_back(
        self,
        *,
        target: AuditTarget,
        context: _AuditAttempt,
        identity: RunIdentity,
        observation: AuditReadObservation,
        publication: AuditPublication,
        refutation_ref: str,
    ) -> None:
        """Return one refuted criterion to unstarted, beside its evidence."""
        issue_key = observation.target.issue.issue_key
        # The re-pinned snapshot, never the pre-tick issue: an instructed
        # refutation has just put the decision classification on this same
        # criterion, and the port compares every fact but the comment stamp.
        criterion = next(
            item.issue
            for item in context.snapshot.targets
            if item.issue.issue_key == issue_key
        )
        head = _head(publication)
        result = await target.reopener.reopen(
            criterion=criterion,
            ref=head,
            job_id=identity.title(),
            publisher=target.publisher,
            interrupted=context.interrupted,
        )
        context.writes.append(result)
        if result.verdict is not AuditVerdict.HOLDS:
            raise AuditClaimReadError(
                "the reopen exhausted canonical write verification"
            )
        await self._log.ainfo(
            "audit_criterion_reopened",
            run_identity=identity.title(),
            criterion=issue_key,
            head_sha=head,
            refutation_ref=refutation_ref,
        )

    async def _summarize(
        self,
        *,
        target: AuditTarget,
        context: _AuditAttempt,
        identity: RunIdentity,
        coverage: AuditCoverageResult,
        deferred: Sequence[AuditDeferred],
        writes: list[WriteBackResult],
        interrupted: list[AuditRepairInput],
    ) -> None:
        """Publish the scope's one verified summary of what this tick did."""
        scope = target.binding.scope

        async def require_current() -> None:
            await target.sweep.require_current(
                context.snapshot,
                (*context.observations, *context.repair_observations),
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
        destination = await self._tracker.read_issue(issue_key=target.report_issue_key)
        if destination.issue_key != target.report_issue_key:
            raise AuditClaimReadError(
                "the audit report destination returned another identity"
            )
        summary = AuditScopeSummary(
            identity=identity,
            coverage=coverage,
            record_refs=tuple(result.artifact.native_ref for result in writes),
            records=tuple(result.artifact for result in writes),
            deferred=tuple(deferred),
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
    ) -> AuditPublication:
        """Publish one report and answer with the publication that landed."""
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
                key = mandate_escalation_key(
                    issue_key=observation.target.issue.issue_key
                )
                # One ref per key is the mapping's own keying, not a branch:
                # the check before create is the tracker's upsert under the
                # occurrence marker, and re-raising against a key already
                # present resolves to that same escalation object.
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
        if final_verdict is AuditVerdict.REFUTED and not reopens_criterion(current):
            # A forge report grades a historical commit, an over-claim or a
            # removal report is about the Check's standing rather than its
            # failure at head, and a terminal report is about the owning
            # issue. None of them is a state move this pass may make.
            raise AuditClaimReadError(
                "the refutation is published; its workflow-state authority "
                "remains unresolved"
            )
        return current

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
