"""Canonical native amendment writes under the actual parent job's lease."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from urllib.parse import unquote

from kodezart.chains.write_back_verifier import (
    FreshWriteBackJudge,
    WriteBackFinding,
    WriteBackResult,
    WriteBackVerifier,
)
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.fire_spec import criterion_field_bodies, replace_criterion_fields
from kodezart.domain.rulings import render_ruling
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.services.owned_workspace import owned_workspace
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.agent import AMENDMENT_TEXT_SCHEMA, Ruling, RulingAuthor
from kodezart.types.domain.amendment import (
    AmendedAmendment,
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentVerdict,
    EscalatedRefusal,
    RecordedRefusal,
    UpheldAmendment,
    UpheldReason,
)
from kodezart.types.domain.amendment_write import (
    AmendmentRecord,
    AmendmentTextOutput,
    CriterionReplacement,
    PreservedSubject,
    RulingReplacement,
)
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment, TrackerIssue


class AmendmentWriteAuthority(Protocol):
    """The writer's live source comparisons, including only its own proved edits."""

    async def require_current(self) -> None:
        """Re-read live criterion/ruling/HEAD/base facts before the next effect."""
        ...

    async def observe_criterion(
        self, *, previous: TrackerIssue, body: str, reset: bool
    ) -> TrackerIssue:
        """Adopt only the exact expected native edit, refusing unrelated drift."""
        ...

    async def observe_ruling(
        self, *, previous: TrackerComment, ruling: Ruling, body: str
    ) -> TrackerComment:
        """Adopt the same native ruling occurrence after its expected edit."""
        ...


@dataclass(frozen=True)
class _Step:
    surface: WritableSurface
    apply: Callable[[WriteBackFinding | None], Awaitable[None]]

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        await self.apply(finding)


class AmendmentWriteBack:
    """Keep history, apply the authorized edit and independently verify what landed."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        operation: OperationConfig,
        max_verify_rounds: int,
        gate: OutboundContentGate,
        lease_seconds: float,
        repo_url: str,
    ) -> None:
        self._tracker, self._runner, self._workspace = tracker, runner, workspace
        self._git, self._prompts, self._skills = git, prompts, skills
        self._operation, self._gate = operation, gate
        self._lease_seconds, self._repo_url = lease_seconds, repo_url
        self._log = get_logger(__name__)
        self._verifier = WriteBackVerifier(
            tracker=tracker,
            judge=FreshWriteBackJudge(
                runner=runner,
                workspace=workspace,
                git=git,
                prompts=prompts,
                skills=skills,
                repo_url=repo_url,
                session_type=SessionType.TICKET_FIRE,
            ),
            max_rounds=max_verify_rounds,
        )
        self._escalations = LaneEscalationWriter(
            tracker=tracker,
            gate=gate,
            operation=operation,
            surface_lease_seconds=lease_seconds,
        )

    async def _author(
        self,
        *,
        claim: AmendmentClaim,
        judgment: AmendmentJudgment,
        prior: TrackerArtifact,
        finding: WriteBackFinding | None,
        preserve: bool,
    ) -> AmendmentTextOutput:
        prompt = self._prompts.template_for(PromptKey.AMENDMENT_AUTHOR).render(
            {
                "claim": claim.model_dump_json(),
                "judgment": judgment.model_dump_json(),
                "prior": prior.model_dump_json(),
                "finding": "No prior write-back finding." if finding is None
                else finding.model_dump_json(),
                "preserve_subject": str(preserve).lower(),
            }
        )
        async with owned_workspace(
            self._workspace, repo_url=self._repo_url, ref=judgment.base_sha
        ) as workspace:
            async def require_base() -> None:
                if (
                    await settle(self._git.current_sha(workspace)) != judgment.base_sha
                    or await settle(self._git.has_changes(workspace))
                    or await settle(self._git.has_replace_refs(workspace))
                ):
                    raise NativeWriteRefusalError("Amendment author changed its clean base")

            await require_base()
            payload = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=workspace,
                key=PromptKey.AMENDMENT_AUTHOR,
                prompt=prompt,
                output_schema=AMENDMENT_TEXT_SCHEMA,
                site="amendment_author",
                session_type=SessionType.TICKET_FIRE,
                failure_message="No amendment text was returned.",
            )
            output = AmendmentTextOutput.model_validate(payload)
            await require_base()
        if preserve != isinstance(output.replacement, PreservedSubject):
            raise NativeWriteRefusalError("The amendment author exceeded its write role")
        if not isinstance(output.replacement, PreservedSubject) and (
            output.replacement.subject != claim.subject
        ):
            raise NativeWriteRefusalError("The amendment author changed subject identity")
        return output

    async def _gate_exact(
        self, *, body: str, visibility: RepoVisibility, destination: OutboundDestination
    ) -> str:
        result = await gated_write(
            gate=self._gate,
            log=self._log,
            content=body,
            visibility=visibility,
            shape=WriterShape.PROSE,
            destination=destination,
            content_class=ContentClass.AUTHORED,
        )
        if result != body:
            raise NativeWriteRefusalError(
                "The outbound gate changed the exact amendment evidence or text"
            )
        return result

    async def _verify(
        self, *, step: _Step, base: str, authority: AmendmentWriteAuthority
    ) -> WriteBackResult:
        result = await self._verifier.write_back(step=step, ref=base)
        await authority.require_current()
        if result.verdict is not AuditVerdict.HOLDS:
            raise NativeWriteRefusalError(
                "Canonical amendment write-back exhausted its configured bound"
            )
        return result

    async def apply(
        self,
        *,
        claim: AmendmentClaim,
        judgment: AmendmentJudgment,
        reason: UpheldReason | None,
        lane_key: str,
        holder: str,
        visibility: RepoVisibility,
        authority: AmendmentWriteAuthority,
        criterion: TrackerIssue | None,
        ruling_record: tuple[TrackerComment, Ruling] | None,
    ) -> AmendmentVerdict:
        """An actual claimed subject takes exactly one native surface branch."""
        if not holder.strip():
            raise NativeWriteRefusalError("Native amendment writes require the parent job holder")
        if (criterion is None) == (ruling_record is None):
            raise NativeWriteRefusalError("The amendment requires one actual native subject")
        if criterion is not None:
            surface = WritableSurface(
                kind=SurfaceKind.CRITERION_SUB_ISSUE,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=criterion.issue_key),
            )
        else:
            assert ruling_record is not None
            comment, _ = ruling_record
            surface = WritableSurface(
                kind=SurfaceKind.MARKER_COMMENT,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=comment.issue_key),
                marker=comment.body.partition("\n")[0],
            )
        prior = await read_tracker_artifact(tracker=self._tracker, surface=surface)
        await authority.require_current()
        occurrence = sha256(
            (claim.model_dump_json() + judgment.base_sha).encode()
        ).hexdigest()
        marker = compose_comment_marker(
            prefixes=self._operation.marker_prefixes,
            purpose="amendment",
            lane=lane_key,
            occurrence_key=occurrence,
        )
        archive_surface = WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT, ref=surface.ref, marker=marker
        )
        surfaces = {archive_surface}
        if reason is None:
            surfaces.add(surface)
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=holder,
            surfaces=frozenset(surfaces),
            lease_seconds=self._lease_seconds,
        ) as lease:
            await authority.require_current()
            explanation = judgment.finding.refutation or (
                f"The independently judged departure remains {reason.value if reason else 'pending'}"
            )

            async def archive(finding: WriteBackFinding | None) -> None:
                nonlocal explanation
                if finding is not None:
                    text = await self._author(
                        claim=claim, judgment=judgment, prior=prior,
                        finding=finding, preserve=True,
                    )
                    explanation = text.explanation
                record = AmendmentRecord(
                    disposition="proposed_amendment" if reason is None
                    else "accepted_and_not_actioned",
                    claim=claim,
                    judgment=judgment,
                    prior=prior,
                    explanation=explanation,
                )
                content = record.model_dump_json(by_alias=True)
                await self._gate_exact(
                    body=marked_comment_body(marker=marker, body=content),
                    visibility=visibility,
                    destination=OutboundDestination.TRACKER_COMMENT,
                )
                await lease.renew()
                await authority.require_current()
                await settle(self._tracker.upsert_comment(
                    target=surface.ref.key, marker=marker, body=content, holder=holder
                ))

            archive_result = await self._verify(
                step=_Step(surface=archive_surface, apply=archive),
                base=judgment.base_sha, authority=authority,
            )
            if reason is not None:
                publication = RecordedRefusal(record=archive_result)
                if reason is UpheldReason.COST_MEASURED_UNECONOMIC:
                    escalation_result = await self._escalate(
                        claim=claim, judgment=judgment, prior=prior,
                        lane_key=lane_key, holder=holder, occurrence=occurrence,
                        visibility=visibility, authority=authority,
                    )
                    return UpheldAmendment(
                        claim=claim, reason=reason, judgment=judgment,
                        publication=EscalatedRefusal(
                            record=archive_result, escalation=escalation_result
                        ),
                    )
                return UpheldAmendment(
                    claim=claim, reason=reason, judgment=judgment, publication=publication
                )

            current_criterion = criterion
            current_ruling = ruling_record

            async def amend(finding: WriteBackFinding | None) -> None:
                nonlocal current_criterion, current_ruling
                text = await self._author(
                    claim=claim, judgment=judgment, prior=prior,
                    finding=finding, preserve=False,
                )
                await authority.require_current()
                replacement = text.replacement
                if current_criterion is not None and isinstance(replacement, CriterionReplacement):
                    retired = {"Evidence": ""}
                    if criterion_field_bodies(current_criterion.body, field="Class"):
                        retired["Class"] = ""
                    cleared = replace_criterion_fields(current_criterion.body, replacements=retired)
                    await self._gate_exact(
                        body=cleared, visibility=visibility,
                        destination=OutboundDestination.TRACKER_DESCRIPTION,
                    )
                    await lease.renew()
                    await authority.require_current()
                    await settle(self._tracker.edit_description(
                        target=current_criterion.issue_key,
                        expected=current_criterion.body, replacement=cleared,
                    ))
                    current_criterion = await authority.observe_criterion(
                        previous=current_criterion, body=cleared, reset=False
                    )
                    await lease.renew()
                    await authority.require_current()
                    await settle(self._tracker.reset_criterion_pending(
                        expected=current_criterion, holder=holder
                    ))
                    current_criterion = await authority.observe_criterion(
                        previous=current_criterion, body=cleared, reset=True
                    )
                    amended = replace_criterion_fields(
                        cleared, replacements={"Check": replacement.check, "Do": replacement.do}
                    )
                    await self._gate_exact(
                        body=amended, visibility=visibility,
                        destination=OutboundDestination.TRACKER_DESCRIPTION,
                    )
                    await lease.renew()
                    await authority.require_current()
                    await settle(self._tracker.edit_description(
                        target=current_criterion.issue_key,
                        expected=current_criterion.body, replacement=amended,
                    ))
                    current_criterion = await authority.observe_criterion(
                        previous=current_criterion, body=amended, reset=False
                    )
                elif current_ruling is not None and isinstance(replacement, RulingReplacement):
                    comment, ruling = current_ruling
                    changed = Ruling.model_validate({
                        **ruling.model_dump(),
                        "resolution": replacement.resolution,
                        "rejected_alternative": replacement.rejected_alternative,
                        "repo_evidence": replacement.repo_evidence,
                        "authored_by": RulingAuthor.MACHINE,
                    })
                    native_lane = unquote(comment.body.partition("\n")[0][1:-1].split(":")[1])
                    body = render_ruling(
                        ruling=changed, lane_key=native_lane,
                        marker_prefixes=self._operation.marker_prefixes,
                    )
                    await self._gate_exact(
                        body=body, visibility=visibility,
                        destination=OutboundDestination.TRACKER_COMMENT,
                    )
                    await lease.renew()
                    await authority.require_current()
                    marker, _, content = body.partition("\n")
                    await settle(self._tracker.upsert_comment(
                        target=comment.issue_key, marker=marker, body=content,
                        holder=holder, expected=comment,
                    ))
                    current_ruling = (
                        await authority.observe_ruling(previous=comment, ruling=changed, body=body),
                        changed,
                    )
                else:
                    raise NativeWriteRefusalError("The replacement does not address this native subject")

            applied = await self._verify(
                step=_Step(surface=surface, apply=amend),
                base=judgment.base_sha, authority=authority,
            )
            return AmendedAmendment(
                claim=claim, judgment=judgment, prior=prior,
                archive=archive_result, applied=applied,
            )

    async def _escalate(
        self,
        *,
        claim: AmendmentClaim,
        judgment: AmendmentJudgment,
        prior: TrackerArtifact,
        lane_key: str,
        holder: str,
        occurrence: str,
        visibility: RepoVisibility,
        authority: AmendmentWriteAuthority,
    ) -> WriteBackResult:
        marker = compose_comment_marker(
            prefixes=self._operation.marker_prefixes,
            purpose="escalation", lane=lane_key, occurrence_key=occurrence,
        )
        surface = WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT, ref=prior.surface.ref, marker=marker
        )
        question = f"Resolve the measured uneconomic departure for {claim.subject.id}"

        async def write(finding: WriteBackFinding | None) -> None:
            nonlocal question
            if finding is not None:
                text = await self._author(
                    claim=claim, judgment=judgment, prior=prior,
                    finding=finding, preserve=True,
                )
                question = text.explanation
            escalation = LaneEscalation(
                issue_id=surface.ref.key,
                escalation_key=occurrence,
                raised_by=holder,
                question=question,
                interim_reading="UPHELD: retain the existing criterion or pinned ruling.",
                interim_basis=judgment.model_dump_json(),
                raised_at_sha=judgment.base_sha,
            )
            await self._escalations.raise_escalation(
                lane_key=lane_key, job_id=holder, escalation=escalation,
                visibility=visibility, before_write=authority.require_current,
            )

        return await self._verify(
            step=_Step(surface=surface, apply=write),
            base=judgment.base_sha, authority=authority,
        )
