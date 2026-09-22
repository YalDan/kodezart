"""The Organize pipeline over the configured mandate table.

An owner runs the rows it is given: the pre-approval row for the scheduled
pass, the two run-stage rows for the scope run.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from hashlib import sha256

from kodezart.chains.organize import OrganizeAdmission
from kodezart.chains.organize_author import OrganizeAuthor, ProposedWrite
from kodezart.chains.write_back_verifier import (
    WriteBackFinding,
    WriteBackResult,
    WriteBackVerifier,
)
from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WriteBackJudge,
)
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.criterion_creation import criterion_body, existing_criterion
from kodezart.domain.errors import (
    OrganizeDecisionRequiredError,
    OrganizeWriteRefusalError,
    OutboundContentBlockedError,
    SurfaceLeaseError,
    WriteBackReadError,
)
from kodezart.domain.organize import (
    admission_route,
    is_organize_subject,
    organize_gap,
    owes_stage_label,
    stage_pending,
    stage_unlabelled,
)
from kodezart.domain.organize_graph import (
    graph_peers,
    graph_snapshot,
    validate_graph_change,
)
from kodezart.domain.prompt_variables import organize_variables
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    ScopeLabel,
)
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionRoute,
    MandateKind,
    OrganizeAdmissionRequest,
    OrganizeLabelNamespace,
    ResolvedMandateSpec,
    SpecFinding,
    split_label_key,
)
from kodezart.types.domain.organize_graph import (
    GraphProposal,
    MilestoneChange,
    OrganizeContext,
    SplitProposal,
)
from kodezart.types.domain.organize_owner import (
    BodyProposal,
    CriteriaProposal,
    EscalationUnrecordedHalt,
    OrganizeBoundEvidence,
    OrganizePolicy,
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
    StageIncompleteHalt,
    UnavailableProposal,
    UnresolvedProposal,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import (
    DescriptionWriteAuthority,
    SurfaceKind,
    WritableSurface,
)
from kodezart.types.domain.tracker import (
    TrackerIssue,
    TrackerIssueRevision,
    WorkflowStateKind,
)


@dataclass(frozen=True)
class _WriteStep:
    surface: WritableSurface
    apply: Callable[[WriteBackFinding | None], Awaitable[None]]

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        await self.apply(finding)


def _created_context(context: OrganizeContext, child: TrackerIssue) -> OrganizeContext:
    if child.issue_key in context.member_keys:
        return context
    return OrganizeContext(
        scope=context.scope,
        member_keys=tuple(sorted((*context.member_keys, child.issue_key))),
        issues=tuple(
            sorted((*context.issues, child), key=lambda issue: issue.issue_key)
        ),
        ruling_comments=context.ruling_comments,
        milestones=context.milestones,
    )


class OrganizeOwner:
    def __init__(
        self,
        *,
        tracker: TrackerPort,
        context: OrganizeContextReader,
        admission: OrganizeAdmission,
        author: OrganizeAuthor,
        judge: WriteBackJudge,
        gate: OutboundContentGate,
        prompts: PromptSetProvider,
        operation: OperationConfig,
        phases: Sequence[ResolvedMandateSpec],
        policy: OrganizePolicy,
        write_back_max_rounds: int,
        lease_seconds: float,
    ) -> None:
        self._context = context
        self._tracker, self._admission, self._author = tracker, admission, author
        self._gate, self._prompts, self._operation = gate, prompts, operation
        self._policy, self._lease_seconds = policy, lease_seconds
        self._write_back_max_rounds = write_back_max_rounds
        # The rows this owner was given, already in the governed sequence.
        self._phases = tuple(phases)
        if not self._phases:
            raise OperationMemberAbsentError(
                missing="organize_mandates", stops="Organize construction"
            )
        for phase in self._phases:
            namespace, key = split_label_key(phase.spec.gate_label_key)
            if namespace.value == "scope_labels" and key not in {
                member.value for member in ScopeLabel
            }:
                raise OperationMemberAbsentError(
                    missing=phase.spec.gate_label_key,
                    stops="native scope-label gate read",
                )
        # The body marker is the whole table's, not this owner's rows': the
        # gap arithmetic needs it in every phase, including one whose rows do
        # not include the phase that writes it.
        self._body_marker = next(
            split_label_key(phase.spec.terminal_marker_key)[1]
            for phase in operation.resolve_organize_mandates()
            if phase.role.marks_specification_body
        )
        self._verifier = WriteBackVerifier(
            tracker=tracker, judge=judge, max_rounds=write_back_max_rounds
        )
        self._escalations = LaneEscalationWriter(
            tracker=tracker,
            gate=gate,
            operation=operation,
            surface_lease_seconds=lease_seconds,
        )
        self._admissions: dict[MandateKind, dict[str, AdmissionResult]] = {}
        self._log = get_logger(__name__)

    async def _snapshot(self, scope: ScopeRef) -> tuple[TrackerIssueRevision, ...]:
        members = await self._tracker.scope_issues(ref=scope)
        keys = {member.issue_key for member in members}
        if len(keys) != len(members):
            raise OrganizeWriteRefusalError(
                issue_key=scope.key, reason="duplicate scope identities"
            )
        revisions = tuple(
            [
                await self._tracker.read_issue_revision(issue_key=member.issue_key)
                for member in members
            ]
        )
        if any(
            revision.issue.issue_key != member.issue_key
            for revision, member in zip(revisions, members, strict=True)
        ):
            raise OrganizeWriteRefusalError(
                issue_key=scope.key, reason="scope revision identity changed"
            )
        # Breadth first; unrelated roots retain their stable native order.
        by_key = {r.issue.issue_key: r for r in revisions}

        def depth(revision: TrackerIssueRevision) -> int:
            seen = {revision.issue.issue_key}
            parent = revision.issue.parent_key
            while parent in by_key:
                if parent in seen:
                    raise OrganizeWriteRefusalError(
                        issue_key=scope.key, reason="scope parent cycle"
                    )
                seen.add(parent)
                parent = by_key[parent].issue.parent_key
            return len(seen)

        return tuple(
            sorted(
                revisions,
                key=lambda revision: (
                    depth(revision),
                    revision.issue.created_at,
                    revision.issue.issue_key,
                ),
            )
        )

    async def _request(
        self,
        issue: TrackerIssue,
        phase: ResolvedMandateSpec,
        *,
        repo_url: str,
        base_ref: str,
        job_id: str,
        classes: set[str],
        scope: ScopeRef,
    ) -> OrganizeAdmissionRequest:
        linked = [
            await self._tracker.read_issue(issue_key=key)
            for key in sorted(
                {r.issue_key for r in issue.relations} - {issue.issue_key}
            )
        ]
        criteria = await self._tracker.read_criteria(issue_key=issue.issue_key)
        rubric = self._prompts.template_for(phase.spec.rubric_prompt_key).render(
            {
                **organize_variables(
                    graph_context=(
                        await self._context.read(scope=scope)
                    ).model_dump_json(),
                    mandate_rubric="",
                    issue_body=issue.body,
                    linked_issue_bodies=[i.body for i in linked],
                    criterion_issue_bodies=[i.body for i in criteria],
                    refusal_evidence=None,
                    defect_classes=tuple(sorted(classes)),
                ),
                "issue_key": issue.issue_key,
                "base_ref": base_ref,
            }
        )
        return OrganizeAdmissionRequest(
            scope=scope,
            issue_key=issue.issue_key,
            mandate_rubric=rubric,
            repo_url=repo_url,
            base_ref=base_ref,
            cache_key=job_id,
            defect_classes=tuple(sorted(classes)),
            admission_prompt_key=phase.spec.admission_prompt_key,
        )

    async def _route(
        self,
        result: AdmissionResult,
        *,
        issue: TrackerIssue,
        scope_issue_keys: frozenset[str],
    ) -> AdmissionRoute:
        if any(finding.issue_id not in scope_issue_keys for finding in result.findings):
            raise OrganizeWriteRefusalError(
                issue_key=issue.issue_key,
                reason="finding names an issue outside the admitted scope",
            )
        current = await self._tracker.read_issue(issue_key=issue.issue_key)
        if not await self._admission.is_live(result):
            return AdmissionRoute.REAUTHOR
        return admission_route(result, issue=current, scope_issue_keys=scope_issue_keys)

    async def _proof_live(
        self, scope: ScopeRef, judgments: Sequence[AdmissionResult]
    ) -> bool:
        revisions = await self._snapshot(scope)
        selected = {
            r.issue.issue_key: r.issue
            for r in revisions
            if is_organize_subject(r.issue) or "criterion" in r.issue.issue_labels
        }
        if set(selected) != {result.issue_id for result in judgments}:
            return False
        members = frozenset(r.issue.issue_key for r in revisions)
        return all(
            [
                not result.findings
                and await self._route(
                    result, issue=selected[result.issue_id], scope_issue_keys=members
                )
                is AdmissionRoute.MARK_COMPLETE
                for result in judgments
            ]
        )

    async def _admitted(
        self,
        issue: TrackerIssue,
        *,
        phase: ResolvedMandateSpec,
        scope_labels: frozenset[ScopeLabel],
    ) -> bool:
        """Whether this phase may act on *issue* now.

        The one reading behind every gate and approval arm. A run stage is
        admitted by approval and by its gate; a pre-approval phase by its
        gate while approval is absent. A gate naming the approval label
        reads the cascade, never the exact scope's own labels.
        """
        approved = await self._tracker.execution_approved(issue_key=issue.issue_key)
        namespace, key = split_label_key(phase.spec.gate_label_key)
        if (
            namespace is OrganizeLabelNamespace.SCOPE
            and key == ScopeLabel.APPROVED.value
        ):
            gate_open = approved
        elif namespace is OrganizeLabelNamespace.SCOPE:
            gate_open = ScopeLabel(key) in scope_labels
        else:
            gate_open = key in issue.issue_labels
        return gate_open and (
            approved if phase.role.runs_under_approval else not approved
        )

    async def _may_write(
        self, issue_key: str, *, phase: ResolvedMandateSpec, scope: ScopeRef
    ) -> None:
        members = await self._tracker.scope_issues(ref=scope)
        if issue_key not in {member.issue_key for member in members}:
            raise OrganizeWriteRefusalError(
                issue_key=issue_key, reason="the write target left the admitted scope"
            )
        issue = await self._tracker.read_issue(issue_key=issue_key)
        scope_labels = await self._tracker.read_scope_labels(ref=scope)
        if not await self._admitted(issue, phase=phase, scope_labels=scope_labels):
            raise OrganizeWriteRefusalError(
                issue_key=issue_key,
                reason=f"{phase.spec.kind.value} is not admitted for this issue now",
            )

    async def _require_revision(self, revision: TrackerIssueRevision) -> None:
        current = await self._tracker.read_issue_revision(
            issue_key=revision.issue.issue_key
        )
        if (
            current.issue.issue_key != revision.issue.issue_key
            or current.body_digest != revision.body_digest
        ):
            raise OrganizeWriteRefusalError(
                issue_key=revision.issue.issue_key,
                reason="the author's source revision changed before writing",
            )

    async def _author_write(
        self,
        request: OrganizeAdmissionRequest,
        *,
        key: PromptKey,
        phase: ResolvedMandateSpec,
        scope: ScopeRef,
        job_id: str,
        evidence: str | None,
        visibility: RepoVisibility,
    ) -> WriteBackResult:
        initial = await self._author.propose(request, key=key, evidence=evidence)
        initial_value = initial.proposal.root
        declared_peers = (
            graph_peers(initial.revision.issue, initial_value.changes)
            if isinstance(initial_value, GraphProposal)
            else frozenset()
        )
        kind = (
            SurfaceKind.CRITERION_CHILD_SET
            if isinstance(initial_value, CriteriaProposal)
            else SurfaceKind.ISSUE_GRAPH
            if isinstance(initial_value, GraphProposal)
            else SurfaceKind.ISSUE_SPLIT_SET
            if isinstance(initial_value, SplitProposal)
            else SurfaceKind.ISSUE_DESCRIPTION
        )
        surface = WritableSurface(
            kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=request.issue_key)
        )

        async def require_context(proposal: ProposedWrite) -> None:
            await self._require_revision(proposal.revision)
            if not await self._context.matches(
                scope=scope, digest=self._context.digest(proposal.context)
            ):
                raise OrganizeWriteRefusalError(
                    issue_key=request.issue_key,
                    reason="the author's graph context changed before writing",
                )

        async def authorize(proposal: ProposedWrite, peers: frozenset[str]) -> None:
            for peer in sorted(peers):
                await self._may_write(peer, phase=phase, scope=scope)
            await require_context(proposal)
            await self._may_write(request.issue_key, phase=phase, scope=scope)

        async def apply(finding: WriteBackFinding | None) -> None:
            proposal = (
                initial
                if finding is None
                else await self._author.propose(
                    request,
                    key=key,
                    evidence=finding.model_dump_json(),
                )
            )
            value = proposal.proposal.root
            await require_context(proposal)
            await self._may_write(request.issue_key, phase=phase, scope=scope)
            if isinstance(value, UnavailableProposal):
                raise OrganizeWriteRefusalError(
                    issue_key=value.issue_id,
                    reason=(
                        f"unavailable capability {value.capability}: {value.evidence}"
                    ),
                )
            if isinstance(value, UnresolvedProposal):
                raise OrganizeDecisionRequiredError(
                    issue_key=value.issue_id,
                    question=value.question,
                    evidence=value.evidence,
                )
            expected_type = {
                SurfaceKind.ISSUE_DESCRIPTION: BodyProposal,
                SurfaceKind.CRITERION_CHILD_SET: CriteriaProposal,
                SurfaceKind.ISSUE_GRAPH: GraphProposal,
                SurfaceKind.ISSUE_SPLIT_SET: SplitProposal,
            }[kind]
            if (
                not isinstance(value, expected_type)
                or (
                    key is PromptKey.ORGANIZE_CRITERIA_AUTHOR
                    and not isinstance(value, CriteriaProposal)
                )
                or (
                    key is not PromptKey.ORGANIZE_CRITERIA_AUTHOR
                    and isinstance(value, CriteriaProposal)
                )
            ):
                raise OrganizeWriteRefusalError(
                    issue_key=request.issue_key,
                    reason="author returned another write surface",
                )
            if isinstance(value, GraphProposal):
                for change in value.changes:
                    if (
                        isinstance(change, MilestoneChange)
                        and change.milestone_id is not None
                        and change.milestone_id
                        not in {item.ref.key for item in proposal.context.milestones}
                    ):
                        raise OrganizeWriteRefusalError(
                            issue_key=request.issue_key,
                            reason=(
                                "the milestone identity was not in the author's "
                                "current context"
                            ),
                        )
                _, peers = validate_graph_change(
                    issue_key=request.issue_key,
                    changes=value.changes,
                    issues=proposal.context.issues,
                    member_keys=frozenset(proposal.context.member_keys),
                )
                if peers != declared_peers:
                    raise OrganizeWriteRefusalError(
                        issue_key=request.issue_key,
                        reason="graph repair returned another set of affected surfaces",
                    )
                surfaces = frozenset(
                    WritableSurface(
                        kind=SurfaceKind.ISSUE_GRAPH,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=peer),
                    )
                    for peer in peers
                )
                async with RunSurfaceLease(
                    tracker=self._tracker,
                    job_id=job_id,
                    surfaces=surfaces,
                    lease_seconds=self._lease_seconds,
                ) as lease:
                    await lease.renew()
                    await authorize(proposal, peers)
                    await settle(
                        self._tracker.update_issue_graph(
                            issue_key=request.issue_key,
                            expected=tuple(
                                graph_snapshot(issue)
                                for issue in proposal.context.issues
                            ),
                            changes=value.changes,
                            holder=job_id,
                            revalidate=partial(authorize, proposal, peers),
                        )
                    )
                return
            if isinstance(value, SplitProposal):
                # Gate every proposed child before any creation; an existing identity
                # is returned unchanged by the actual create-only port boundary.
                for child in value.children:
                    for content, destination in (
                        (child.title, OutboundDestination.TRACKER_TITLE),
                        (child.body, OutboundDestination.TRACKER_DESCRIPTION),
                    ):
                        gated = await gated_write(
                            gate=self._gate,
                            log=self._log,
                            content=content,
                            visibility=visibility,
                            shape=WriterShape.PROSE,
                            destination=destination,
                            content_class=ContentClass.AUTHORED,
                            aggregates=(),
                        )
                        if gated != content:
                            raise OrganizeWriteRefusalError(
                                issue_key=request.issue_key,
                                reason="outbound gate changed split specification",
                            )
                expected_context = proposal.context
                async with RunSurfaceLease(
                    tracker=self._tracker,
                    job_id=job_id,
                    surfaces=frozenset({surface}),
                    lease_seconds=self._lease_seconds,
                ) as lease:
                    for child in value.children:
                        await lease.renew()
                        await authorize(
                            ProposedWrite(
                                context=expected_context,
                                revision=proposal.revision,
                                proposal=proposal.proposal,
                            ),
                            frozenset({request.issue_key}),
                        )
                        created = await settle(
                            self._tracker.create_split_if_absent(
                                source_key=request.issue_key,
                                deliverable_key=child.deliverable_key,
                                title=child.title,
                                body=child.body,
                                holder=job_id,
                                revalidate=partial(
                                    authorize,
                                    ProposedWrite(
                                        context=expected_context,
                                        revision=proposal.revision,
                                        proposal=proposal.proposal,
                                    ),
                                    frozenset({request.issue_key}),
                                ),
                                expected=tuple(
                                    graph_snapshot(issue)
                                    for issue in expected_context.issues
                                ),
                            )
                        )
                        expected_context = _created_context(expected_context, created)
                return
            if isinstance(value, BodyProposal):
                content = await gated_write(
                    gate=self._gate,
                    log=self._log,
                    content=value.body,
                    visibility=visibility,
                    shape=WriterShape.PROSE,
                    destination=OutboundDestination.TRACKER_DESCRIPTION,
                    content_class=ContentClass.AUTHORED,
                    aggregates=(),
                )
                if content == proposal.revision.issue.body:
                    return
                async with RunSurfaceLease(
                    tracker=self._tracker,
                    job_id=job_id,
                    surfaces=frozenset({surface}),
                    lease_seconds=self._lease_seconds,
                ) as lease:
                    await lease.renew()
                    await authorize(proposal, frozenset({request.issue_key}))
                    await settle(
                        self._tracker.edit_description(
                            target=request.issue_key,
                            expected=proposal.revision.issue.body,
                            replacement=content,
                            authorization=DescriptionWriteAuthority(
                                holder=job_id,
                                surface=surface,
                                revalidate=partial(
                                    authorize, proposal, frozenset({request.issue_key})
                                ),
                            ),
                        )
                    )
            elif isinstance(value, CriteriaProposal):
                children = await self._tracker.read_criteria(
                    issue_key=request.issue_key
                )
                missing = [
                    item
                    for item in value.criteria
                    if existing_criterion(
                        parent_key=request.issue_key,
                        check=item.check,
                        children=children,
                    )
                    is None
                ]
                if not missing:
                    return
                if len({item.check.strip() for item in missing}) != len(missing):
                    raise OrganizeWriteRefusalError(
                        issue_key=request.issue_key,
                        reason="duplicate proposed Check identities",
                    )
                for item in missing:
                    body = criterion_body(
                        parent_key=request.issue_key, check=item.check, do=item.do
                    )
                    gated = await gated_write(
                        gate=self._gate,
                        log=self._log,
                        content=body,
                        visibility=visibility,
                        shape=WriterShape.PROSE,
                        destination=OutboundDestination.TRACKER_DESCRIPTION,
                        content_class=ContentClass.AUTHORED,
                        aggregates=(),
                    )
                    gated_title = await gated_write(
                        gate=self._gate,
                        log=self._log,
                        content=item.title,
                        visibility=visibility,
                        shape=WriterShape.PROSE,
                        destination=OutboundDestination.TRACKER_TITLE,
                        content_class=ContentClass.AUTHORED,
                        aggregates=(),
                    )
                    if gated != body or gated_title != item.title:
                        raise OrganizeWriteRefusalError(
                            issue_key=request.issue_key,
                            reason="outbound gate changed criterion specification",
                        )
                expected_context = proposal.context
                async with RunSurfaceLease(
                    tracker=self._tracker,
                    job_id=job_id,
                    surfaces=frozenset({surface}),
                    lease_seconds=self._lease_seconds,
                ) as lease:
                    for item in missing:
                        await lease.renew()
                        await authorize(
                            ProposedWrite(
                                context=expected_context,
                                revision=proposal.revision,
                                proposal=proposal.proposal,
                            ),
                            frozenset({request.issue_key}),
                        )
                        created = await settle(
                            self._tracker.create_criterion_if_absent(
                                parent_key=request.issue_key,
                                title=item.title,
                                check=item.check,
                                do=item.do,
                                holder=job_id,
                                revalidate=partial(
                                    authorize,
                                    ProposedWrite(
                                        context=expected_context,
                                        revision=proposal.revision,
                                        proposal=proposal.proposal,
                                    ),
                                    frozenset({request.issue_key}),
                                ),
                            )
                        )
                        expected_context = _created_context(expected_context, created)

        result = await self._verifier.write_back(
            step=_WriteStep(surface, apply), ref=request.base_ref
        )
        if result.verdict is not AuditVerdict.HOLDS or not isinstance(
            initial_value, GraphProposal
        ):
            return result
        _, peers = validate_graph_change(
            issue_key=request.issue_key,
            changes=initial_value.changes,
            issues=initial.context.issues,
            member_keys=frozenset(initial.context.member_keys),
        )

        # Removed peers remain addressed from the original actual closure. This
        # confirmation step has no write or repair capability on those peers.
        async def confirm_peer(_finding: WriteBackFinding | None) -> None:
            return None

        for peer in sorted(peers - {request.issue_key}):
            peer_result = await self._verifier.write_back(
                step=_WriteStep(
                    WritableSurface(
                        kind=SurfaceKind.ISSUE_GRAPH,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=peer),
                    ),
                    confirm_peer,
                ),
                ref=request.base_ref,
            )
            if peer_result.verdict is not AuditVerdict.HOLDS:
                return peer_result
        return result

    async def _mark(
        self,
        request: OrganizeAdmissionRequest,
        *,
        phase: ResolvedMandateSpec,
        job_id: str,
        judgments: Sequence[AdmissionResult],
        scope: ScopeRef,
        visibility: RepoVisibility,
    ) -> bool:
        _, marker = split_label_key(phase.spec.terminal_marker_key)
        current = await self._tracker.read_issue(issue_key=request.issue_key)
        if marker in current.issue_labels:
            return True
        surface = WritableSurface(
            kind=SurfaceKind.ISSUE_LABEL_SET,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=request.issue_key),
        )

        async def apply(finding: WriteBackFinding | None) -> None:
            if finding is not None:
                raise OrganizeWriteRefusalError(
                    issue_key=request.issue_key,
                    reason="phase marker readback was not verified",
                )
            if not await self._proof_live(scope, judgments):
                raise OrganizeWriteRefusalError(
                    issue_key=request.issue_key,
                    reason="phase evidence changed before marker",
                )
            await self._may_write(request.issue_key, phase=phase, scope=scope)
            # The marker is DERIVED — a configured phase member a process
            # that never held the session recomputes — and it is an
            # IDENTIFIER: a placeholder in its place names no member of the
            # board's vocabulary, so a hit refuses rather than redacts.
            classification = await gated_write(
                gate=self._gate,
                log=self._log,
                content=marker,
                visibility=visibility,
                shape=WriterShape.IDENTIFIER,
                destination=OutboundDestination.TRACKER_CLASSIFICATION,
                content_class=ContentClass.DERIVED,
                aggregates=(),
            )
            if classification != marker:
                raise OrganizeWriteRefusalError(
                    issue_key=request.issue_key,
                    reason="outbound gate changed the phase marker",
                )
            async with RunSurfaceLease(
                tracker=self._tracker,
                job_id=job_id,
                surfaces=frozenset({surface}),
                lease_seconds=self._lease_seconds,
            ) as lease:
                await lease.renew()
                if not await self._proof_live(scope, judgments):
                    raise OrganizeWriteRefusalError(
                        issue_key=request.issue_key,
                        reason="phase evidence changed during lease acquisition",
                    )
                await self._may_write(request.issue_key, phase=phase, scope=scope)
                await settle(
                    self._tracker.set_issue_classification(
                        issue_key=request.issue_key, classification=classification
                    )
                )

        result = await self._verifier.write_back(
            step=_WriteStep(surface, apply), ref=request.base_ref
        )
        return result.verdict is AuditVerdict.HOLDS

    async def _halt(
        self,
        *,
        cause: StageHaltCause,
        questions: Sequence[UnresolvedProposal] = (),
        bound: OrganizeBoundEvidence | None = None,
        write_back_results: Sequence[WriteBackResult] = (),
        results: Sequence[AdmissionResult],
        findings: Sequence[SpecFinding],
        phase: ResolvedMandateSpec,
        scope: ScopeRef,
        job_id: str,
        base_ref: str,
        visibility: RepoVisibility,
    ) -> StageHaltReport:
        records = {
            r.issue_id: (
                r.invented_decision or r.missing_artifact or r.evidence,
                r.evidence,
            )
            for r in results
        }
        records.update(
            {
                f.issue_id: (f.mandate_text or f.defect_class, f.evidence)
                for f in findings
            }
        )
        records.update({q.issue_id: (q.question, q.evidence) for q in questions})
        records.update(
            {
                result.artifact.surface.ref.key: (
                    "The written artifact remains independently unverified.",
                    result.model_dump_json(),
                )
                for result in write_back_results
            }
        )
        for issue_key, (question, evidence) in records.items():
            revision = await self._tracker.read_issue_revision(issue_key=issue_key)
            if revision.issue.issue_key != issue_key:
                raise OrganizeWriteRefusalError(
                    issue_key=issue_key, reason="the escalation source identity changed"
                )

            async def before_write(
                *, revision: TrackerIssueRevision = revision
            ) -> None:
                await self._require_revision(revision)
                if not write_back_results and any(
                    [
                        not await self._admission.is_live(result)
                        for result in results
                        if result.issue_id == revision.issue.issue_key
                    ]
                ):
                    raise OrganizeWriteRefusalError(
                        issue_key=revision.issue.issue_key,
                        reason="the escalation admission evidence is no longer current",
                    )
                await self._may_write(
                    revision.issue.issue_key, phase=phase, scope=scope
                )

            occurrence = sha256(
                f"{phase.spec.kind.value}\n{question}".encode()
            ).hexdigest()
            escalation = LaneEscalation(
                issue_id=issue_key,
                escalation_key=occurrence,
                raised_by=job_id,
                question=question,
                interim_reading=(
                    "Preparation stops; no completion marker "
                    "or execution is authorized."
                ),
                interim_basis=evidence,
                raised_at_sha=base_ref,
            )
            marker = compose_comment_marker(
                prefixes=self._operation.marker_prefixes,
                purpose="escalation",
                lane=issue_key,
                occurrence_key=occurrence,
            )
            surface = WritableSurface(
                kind=SurfaceKind.MARKER_COMMENT,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
                marker=marker,
            )

            async def apply(
                finding: WriteBackFinding | None,
                *,
                issue_key: str = issue_key,
                escalation: LaneEscalation = escalation,
                before_write: Callable[[], Awaitable[None]] = before_write,
            ) -> None:
                if finding is not None:
                    raise WriteBackReadError(
                        "the escalation could not be independently confirmed"
                    )
                await before_write()
                await self._escalations.raise_escalation(
                    lane_key=issue_key,
                    job_id=job_id,
                    escalation=escalation,
                    visibility=visibility,
                    before_write=before_write,
                )

            try:
                result = await self._verifier.write_back(
                    step=_WriteStep(surface, apply), ref=base_ref
                )
                if result.verdict is not AuditVerdict.HOLDS:
                    raise WriteBackReadError("the escalation remains unconfirmed")

                # The existing escalation writer also classifies its owning issue.
                # Verify that distinct landed surface through the same loop.
                async def confirm_label(
                    finding: WriteBackFinding | None, *, issue_key: str = issue_key
                ) -> None:
                    if finding is not None:
                        raise WriteBackReadError(
                            "the escalation classification remains unconfirmed"
                        )
                    current = await self._tracker.read_issue(issue_key=issue_key)
                    if "decision" not in current.issue_labels:
                        raise WriteBackReadError(
                            "the escalation decision classification is absent"
                        )

                label_result = await self._verifier.write_back(
                    step=_WriteStep(
                        WritableSurface(
                            kind=SurfaceKind.ISSUE_LABEL_SET, ref=surface.ref
                        ),
                        confirm_label,
                    ),
                    ref=base_ref,
                )
                if label_result.verdict is not AuditVerdict.HOLDS:
                    raise WriteBackReadError(
                        "the escalation classification remains unconfirmed"
                    )
            except (
                TrackerUnavailableError,
                TrackerAccessDeniedError,
                TrackerProtocolError,
                WriteBackReadError,
                OperationMemberAbsentError,
                OutboundContentBlockedError,
                SurfaceLeaseError,
            ):
                return StageHaltReport(
                    EscalationUnrecordedHalt(
                        cause=StageHaltCause.ESCALATION_UNRECORDED,
                        unrecorded_escalation_issue_ids=(issue_key,),
                        admission_results=tuple(results),
                        questions=tuple(questions),
                        surviving_findings=tuple(findings),
                        write_back_results=tuple(write_back_results),
                    )
                )
        return StageHaltReport.model_validate(
            {
                "cause": cause,
                "bound": bound,
                "questions": questions,
                "admission_results": results,
                "surviving_findings": findings,
                "write_back_results": write_back_results,
            }
        )

    def _roster(
        self,
        *,
        phase: ResolvedMandateSpec,
        issues: Sequence[TrackerIssue],
        unlabelled: Sequence[str],
        finding_keys: set[str],
    ) -> tuple[str, ...]:
        """The members whose admission this round has to read, and no others.

        A pre-approval phase needs every member that owes its marker at all,
        because whether it is open here is decided by who is admitted. A run
        stage needs the unlabelled ones plus any labelled member a finding of
        this run names.
        """
        if not phase.role.runs_under_approval:
            return tuple(issue.issue_key for issue in issues if owes_stage_label(issue))
        return tuple(dict.fromkeys((*unlabelled, *sorted(finding_keys))))

    async def _admissions_for(
        self,
        *,
        keys: Sequence[str],
        by_key: Mapping[str, TrackerIssue],
        phase: ResolvedMandateSpec,
        scope_labels: frozenset[ScopeLabel],
    ) -> dict[str, bool]:
        """One admission reading per named member, through the one predicate."""
        return {
            key: await self._admitted(
                by_key[key], phase=phase, scope_labels=scope_labels
            )
            for key in keys
        }

    def _stage_incomplete(
        self,
        *,
        completed: Sequence[MandateKind],
        phase: ResolvedMandateSpec,
        owed: tuple[str, ...],
    ) -> OrganizeReport:
        """The report-shaped halt for members this stage did not label."""
        return OrganizeReport(
            completed_phases=tuple(completed),
            halt=StageHaltReport(
                StageIncompleteHalt(
                    cause=StageHaltCause.STAGE_INCOMPLETE,
                    phase=phase.spec.kind,
                    unlabelled_issue_ids=owed,
                )
            ),
        )

    async def run(
        self,
        *,
        scope: ScopeRef,
        repo_url: str,
        base_ref: str,
        job_id: str,
        visibility: RepoVisibility,
    ) -> OrganizeReport:
        completed: list[MandateKind] = []
        for phase in self._phases:
            admissions = self._admissions.setdefault(phase.spec.kind, {})
            classes: set[str] = set()
            findings: tuple[SpecFinding, ...] = ()
            marker = split_label_key(phase.spec.terminal_marker_key)[1]
            active = False
            for _convergence_round in range(self._policy.max_convergence_rounds):
                snapshot = await self._snapshot(scope)
                issues = [r.issue for r in snapshot]
                by_key = {issue.issue_key: issue for issue in issues}
                members = set(by_key)
                scope_labels = await self._tracker.read_scope_labels(ref=scope)
                unlabelled = stage_unlabelled(issues=issues, marker=marker)
                finding_keys = {f.issue_id for f in findings}
                admitted = await self._admissions_for(
                    keys=self._roster(
                        phase=phase,
                        issues=issues,
                        unlabelled=unlabelled,
                        finding_keys=finding_keys & members,
                    ),
                    by_key=by_key,
                    phase=phase,
                    scope_labels=scope_labels,
                )
                pending = stage_pending(
                    unlabelled=unlabelled,
                    admitted=admitted,
                    under_approval=phase.role.runs_under_approval,
                )
                if pending is None:
                    # Nobody is admitted here: an approved scope on the
                    # pre-approval row, or one whose gate is absent. No work,
                    # no completion, no halt.
                    break
                active = True
                blocked = tuple(
                    key
                    for key in pending
                    if not admitted.get(key, False)
                    or not is_organize_subject(by_key[key])
                )
                if blocked:
                    # Counted, named and free, before any session opens.
                    return self._stage_incomplete(
                        completed=completed, phase=phase, owed=blocked
                    )
                gap = organize_gap(
                    revisions=snapshot,
                    admissions=tuple(
                        [
                            result
                            for result in admissions.values()
                            if await self._admission.is_live(result)
                        ]
                    ),
                    open_findings=findings,
                    body_marker_key=self._body_marker,
                )
                # A member already carrying the marker is out of the roster
                # unless a finding of this run names it: the label is the
                # durable record of the admission test that set it.
                subjects = [
                    issue
                    for issue in issues
                    if is_organize_subject(issue)
                    and admitted.get(issue.issue_key, False)
                    and (issue.issue_key in pending or issue.issue_key in finding_keys)
                ]
                if not subjects:
                    break
                work = {issue.issue_key for issue in gap}
                for issue in subjects:
                    if issue.issue_key not in work:
                        continue
                    request = await self._request(
                        issue,
                        phase,
                        repo_url=repo_url,
                        base_ref=base_ref,
                        job_id=job_id,
                        classes=classes,
                        scope=scope,
                    )
                    result = await self._admission.assess(request)
                    pending_findings = tuple(
                        f
                        for f in findings
                        if f.issue_id == issue.issue_key
                        or any(
                            r.issue.issue_key == f.issue_id
                            and r.issue.parent_key == issue.issue_key
                            for r in snapshot
                        )
                    )
                    key = phase.role.author_prompt_key
                    for _admission_round in range(self._policy.max_admission_rounds):
                        route = await self._route(
                            result, issue=issue, scope_issue_keys=frozenset(members)
                        )
                        if route is AdmissionRoute.ESCALATE:
                            halt = await self._halt(
                                cause=StageHaltCause.HUMAN_DECISION,
                                results=(result,),
                                findings=(),
                                phase=phase,
                                scope=scope,
                                job_id=job_id,
                                base_ref=base_ref,
                                visibility=visibility,
                            )
                            return OrganizeReport(
                                completed_phases=tuple(completed), halt=halt
                            )
                        children = await self._tracker.read_criteria(
                            issue_key=issue.issue_key
                        )
                        needs_criteria = (
                            key is PromptKey.ORGANIZE_CRITERIA_AUTHOR
                            and not any(
                                c.state_kind is not WorkflowStateKind.CANCELED
                                for c in children
                            )
                        )
                        if (
                            route is AdmissionRoute.MARK_COMPLETE
                            and not result.findings
                            and not pending_findings
                            and not needs_criteria
                        ):
                            break
                        try:
                            verified_write = await self._author_write(
                                request,
                                key=key,
                                phase=phase,
                                scope=scope,
                                job_id=job_id,
                                evidence="\n".join(
                                    (
                                        result.evidence,
                                        *(
                                            f.model_dump_json()
                                            for f in pending_findings
                                        ),
                                    )
                                ),
                                visibility=visibility,
                            )
                        except OrganizeDecisionRequiredError as exc:
                            halt = await self._halt(
                                cause=StageHaltCause.HUMAN_DECISION,
                                results=(),
                                findings=(),
                                questions=(
                                    UnresolvedProposal(
                                        kind="unresolved",
                                        issue_id=exc.issue_key,
                                        question=exc.question,
                                        evidence=exc.evidence,
                                    ),
                                ),
                                phase=phase,
                                scope=scope,
                                job_id=job_id,
                                base_ref=base_ref,
                                visibility=visibility,
                            )
                            return OrganizeReport(
                                completed_phases=tuple(completed), halt=halt
                            )
                        except SurfaceLeaseError as unheld:
                            # Another holder owns a surface this write needs,
                            # inside the admitted scope (membership and the gate
                            # were re-asked before the write). The round repairs
                            # nothing here; the dry round reports the class
                            # again and the bound reports it with its finding.
                            await self._log.awarning(
                                "organize_surface_unheld",
                                issue_key=request.issue_key,
                                phase=phase.spec.kind.value,
                                surface_kind=unheld.surface_kind,
                                current_holder=unheld.current_holder,
                            )
                            break
                        if verified_write.verdict is not AuditVerdict.HOLDS:
                            halt = await self._halt(
                                cause=StageHaltCause.ADMISSION_EXHAUSTED,
                                bound=OrganizeBoundEvidence(
                                    setting="write_back.max_verify_rounds",
                                    value=self._write_back_max_rounds,
                                    rounds_used=len(verified_write.rounds),
                                    loop="write_back",
                                ),
                                write_back_results=(verified_write,),
                                results=(result,),
                                findings=(),
                                phase=phase,
                                scope=scope,
                                job_id=job_id,
                                base_ref=base_ref,
                                visibility=visibility,
                            )
                            return OrganizeReport(
                                completed_phases=tuple(completed), halt=halt
                            )
                        # Parent edits may remove this subject; splits may add
                        # newly minted members. Continue on the actual membership.
                        refreshed = await self._snapshot(scope)
                        members = {revision.issue.issue_key for revision in refreshed}
                        if request.issue_key not in members:
                            admissions.pop(request.issue_key, None)
                            break
                        result = await self._admission.verify(request)
                        route = await self._route(
                            result, issue=issue, scope_issue_keys=frozenset(members)
                        )
                        children = await self._tracker.read_criteria(
                            issue_key=issue.issue_key
                        )
                        if (
                            route is AdmissionRoute.MARK_COMPLETE
                            and not result.findings
                            and (
                                key is not PromptKey.ORGANIZE_CRITERIA_AUTHOR
                                or any(
                                    c.state_kind is not WorkflowStateKind.CANCELED
                                    for c in children
                                )
                            )
                        ):
                            break
                    else:
                        halt = await self._halt(
                            cause=StageHaltCause.ADMISSION_EXHAUSTED,
                            bound=OrganizeBoundEvidence(
                                setting="organize.max_admission_rounds",
                                value=self._policy.max_admission_rounds,
                                rounds_used=_admission_round + 1,
                                loop="admission",
                            ),
                            results=(result,),
                            findings=(),
                            phase=phase,
                            scope=scope,
                            job_id=job_id,
                            base_ref=base_ref,
                            visibility=visibility,
                        )
                        return OrganizeReport(
                            completed_phases=tuple(completed), halt=halt
                        )
                # A dry round is a new verification of the whole scope,
                # including surfaces untouched by this round's author.
                current = await self._snapshot(scope)
                members = {r.issue.issue_key for r in current}
                fresh: list[AdmissionResult] = []
                refused: list[AdmissionResult] = []
                for revision in current:
                    issue = revision.issue
                    if (
                        not is_organize_subject(issue)
                        and "criterion" not in issue.issue_labels
                    ):
                        continue
                    request = await self._request(
                        issue,
                        phase,
                        repo_url=repo_url,
                        base_ref=base_ref,
                        job_id=job_id,
                        classes=classes,
                        scope=scope,
                    )
                    result = await self._admission.verify(request)
                    fresh.append(result)
                    if (
                        await self._route(
                            result, issue=issue, scope_issue_keys=frozenset(members)
                        )
                        is not AdmissionRoute.MARK_COMPLETE
                    ):
                        refused.append(result)
                findings = tuple(f for result in fresh for f in result.findings)
                classes.update(f.defect_class for f in findings)
                refused_keys = {r.issue_id for r in refused}
                for result in fresh:
                    if result.issue_id in refused_keys or result.findings:
                        admissions.pop(result.issue_id, None)
                    else:
                        admissions[result.issue_id] = result
                latest_members = {
                    r.issue.issue_key for r in await self._snapshot(scope)
                }
                if (
                    latest_members == members
                    and not refused
                    and not findings
                    and all([await self._admission.is_live(result) for result in fresh])
                ):
                    # Newly prepared split children belong to this same phase;
                    # removed members no longer receive its marker.
                    current_labels = await self._tracker.read_scope_labels(ref=scope)
                    marker_subjects = [
                        revision.issue
                        for revision in current
                        if is_organize_subject(revision.issue)
                        and await self._admitted(
                            revision.issue, phase=phase, scope_labels=current_labels
                        )
                    ]
                    for issue in marker_subjects:
                        request = await self._request(
                            issue,
                            phase,
                            repo_url=repo_url,
                            base_ref=base_ref,
                            job_id=job_id,
                            classes=classes,
                            scope=scope,
                        )
                        if not await self._mark(
                            request,
                            phase=phase,
                            job_id=job_id,
                            judgments=fresh,
                            scope=scope,
                            visibility=visibility,
                        ):
                            raise OrganizeWriteRefusalError(
                                issue_key=issue.issue_key,
                                reason="phase marker remains unverified",
                            )
                    break
            else:
                halt = await self._halt(
                    cause=StageHaltCause.CONVERGENCE_EXHAUSTED,
                    bound=OrganizeBoundEvidence(
                        setting="organize.max_convergence_rounds",
                        value=self._policy.max_convergence_rounds,
                        rounds_used=_convergence_round + 1,
                        loop="convergence",
                    ),
                    results=refused,
                    findings=findings,
                    phase=phase,
                    scope=scope,
                    job_id=job_id,
                    base_ref=base_ref,
                    visibility=visibility,
                )
                return OrganizeReport(completed_phases=tuple(completed), halt=halt)
            if not active:
                continue
            # The barrier: read the board again and require the marker on
            # every member that owes it, whatever its admission now.
            settled = [revision.issue for revision in await self._snapshot(scope)]
            settled_labels = await self._tracker.read_scope_labels(ref=scope)
            owed = stage_unlabelled(issues=settled, marker=marker)
            left = stage_pending(
                unlabelled=owed,
                admitted=await self._admissions_for(
                    keys=owed,
                    by_key={issue.issue_key: issue for issue in settled},
                    phase=phase,
                    scope_labels=settled_labels,
                ),
                under_approval=phase.role.runs_under_approval,
            )
            if left:
                return self._stage_incomplete(
                    completed=completed, phase=phase, owed=left
                )
            completed.append(phase.spec.kind)
        return OrganizeReport(completed_phases=tuple(completed))
