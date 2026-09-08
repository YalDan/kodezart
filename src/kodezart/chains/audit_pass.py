"""Fresh current-head claim judgments, before full sweep publication."""

import asyncio
import json

from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    RepoCache,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import AuditClaimReadError, CriterionResolutionError
from kodezart.domain.fire_spec import criterion_check
from kodezart.services.criterion_sources import resolve_criterion
from kodezart.services.git_observations import (
    read_remote_head,
    read_replace_refs,
    read_workspace_head,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.repo_observations import ensure_repository
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA, AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimObservation,
    AuditClaimReport,
    AuditClaimRequest,
    AuditMandateJudgment,
    AuditMandateObservation,
    AuditMandateRequest,
    AuditVerdict,
    TrackerArtifact,
    UnreadableAuditSurface,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.surface import WritableSurface
from kodezart.types.domain.tracker import TrackerIssue


class AuditClaimVerifier:
    """Re-read the current Check and measure its branch without prior verdicts.

    A returned observation is one input to a later complete sweep. It neither
    advances coverage nor publishes a refutation without its required mandate.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        records: LaneRecordReader,
        cache: RepoCache,
        git: GitService,
        workspace: WorkspaceProvider,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._records = records
        self._cache = cache
        self._git = git
        self._workspace = workspace
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._remote = config.git_remote

    async def _criterion(self, request: AuditClaimRequest) -> TrackerIssue:
        try:
            return await resolve_criterion(
                tracker=self._tracker,
                issue_key=request.lane_issue_key,
                criterion_key=request.criterion_key,
            )
        except CriterionResolutionError as exc:
            raise AuditClaimReadError(str(exc)) from exc

    async def verify(self, request: AuditClaimRequest) -> AuditClaimObservation:
        """Judge the current Check at an exact, live remote head in a fresh session."""
        criterion = await self._criterion(request)
        check = criterion_check(criterion=criterion, issue_key=request.lane_issue_key)
        comment, record = await self._records.read(
            issue_key=request.lane_issue_key,
            lane_key=request.lane_key,
            record_ref=request.record_ref,
        )
        repository = await ensure_repository(
            cache=self._cache, repo_url=request.repo_url, cache_key=request.cache_key
        )
        head = await read_remote_head(
            git=self._git,
            repository=repository,
            remote=self._remote,
            branch=record.branch,
        )
        if head is None or not head.strip():
            raise AuditClaimReadError("the recorded branch has no live remote head")
        key = PromptKey.AUDIT_CLAIM
        prompt = self._prompts.template_for(key).render(
            {"criterion_key": criterion.issue_key, "head_sha": head, "check": check}
        )
        workspace, cancelled = await finish_owned(
            asyncio.create_task(
                self._workspace.acquire(
                    repo_path=repository,
                    ref=head,
                    create_branch=False,
                )
            )
        )
        try:
            if cancelled:
                raise asyncio.CancelledError
            if await read_replace_refs(git=self._git, workspace=workspace):
                raise AuditClaimReadError(
                    "the audit repository substitutes Git objects"
                )
            if await read_workspace_head(git=self._git, workspace=workspace) != (
                head,
                False,
            ):
                raise AuditClaimReadError(
                    "the audit workspace is not clean at the selected head"
                )
            result, rate_limited = await drain(
                self._runner.stream_in_workspace(
                    prompt=prompt,
                    workspace_path=workspace,
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=list(EVAL_TOOLS),
                    skills=self._prompts.session_skills(key, self._skills),
                    session_type=SessionType.SCHEDULED_PASS,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(key),
                    session_id=None,
                    output_format={"type": "json_schema", "schema": AUDIT_CLAIM_SCHEMA},
                ),
                site="audit_claim",
            )
            if (
                result is None
                or result.structured_output is None
                or result.is_error
                or rate_limited
            ):
                raise soft_failure(
                    "Audit claim produced no structured judgment.",
                    raise_site="audit_claim",
                    result_event=result,
                    rate_limit_rejected=rate_limited,
                )
            judgment = AuditClaimJudgment.model_validate(result.structured_output)
            if judgment.criterion_key != criterion.issue_key:
                raise AuditClaimReadError("the judgment names a different criterion")
            if await self._criterion(request) != criterion:
                raise AuditClaimReadError("the criterion changed during verification")
            latest = await self._records.read(
                issue_key=request.lane_issue_key,
                lane_key=request.lane_key,
                record_ref=comment.comment_key,
            )
            if latest != (comment, record):
                raise AuditClaimReadError("the lane record changed during verification")
            if await read_replace_refs(git=self._git, workspace=workspace):
                raise AuditClaimReadError(
                    "the audit repository substitutes Git objects"
                )
            if await read_remote_head(
                git=self._git,
                repository=repository,
                remote=self._remote,
                branch=record.branch,
            ) != head or await read_workspace_head(
                git=self._git, workspace=workspace
            ) != (head, False):
                raise AuditClaimReadError(
                    "the branch or workspace changed during verification"
                )
            return AuditClaimObservation(
                judgment=judgment,
                head_sha=head,
                record_ref=comment.comment_key,
                check=check,
            )
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError


class AuditMandateHunt:
    """Complete current refutations over an explicit native text surface set.

    The audited-set producer and later publication are separate. This consumer
    reads and reports the instruction; it has no write or repair action.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._tracker = tracker
        self._runner = runner
        self._workspace = workspace
        self._git = git
        self._prompts = prompts
        self._skills = skills

    async def _read_set(
        self, surfaces: tuple[WritableSurface, ...]
    ) -> tuple[tuple[TrackerArtifact, ...], tuple[UnreadableAuditSurface, ...]]:
        covered: list[TrackerArtifact] = []
        unreadable: list[UnreadableAuditSurface] = []
        for surface in surfaces:
            try:
                covered.append(
                    await read_tracker_artifact(tracker=self._tracker, surface=surface)
                )
            except Exception as exc:
                # A failed addressed read is coverage failure, never absence.
                # Cancellation is BaseException and always propagates.
                unreadable.append(
                    UnreadableAuditSurface(
                        surface=surface, reason=f"{type(exc).__name__}: {exc}"
                    )
                )
        return tuple(covered), tuple(unreadable)

    async def complete(self, request: AuditMandateRequest) -> AuditClaimReport:
        if request.claim.judgment.verdict is not AuditVerdict.REFUTED:
            return AuditClaimReport(claim=request.claim, mandate=None)
        covered, unreadable = await self._read_set(request.surfaces)
        if unreadable:
            return AuditClaimReport(
                claim=request.claim,
                mandate=AuditMandateObservation(
                    verdict=AuditVerdict.UNVERIFIABLE,
                    covered=covered,
                    unreadable=unreadable,
                    finding=None,
                    finding_surface=None,
                    evidence="The addressed surface set could not be fully read.",
                ),
            )
        workspace, cancelled = await finish_owned(
            asyncio.create_task(
                self._workspace.acquire(
                    repo_url=request.repo_url,
                    ref=request.claim.head_sha,
                    create_branch=False,
                    cache_key=request.cache_key,
                )
            )
        )
        try:
            if cancelled:
                raise asyncio.CancelledError
            if await read_replace_refs(git=self._git, workspace=workspace):
                raise AuditClaimReadError(
                    "the mandate repository substitutes Git objects"
                )
            if await read_workspace_head(git=self._git, workspace=workspace) != (
                request.claim.head_sha,
                False,
            ):
                raise AuditClaimReadError(
                    "mandate workspace is not clean at the observed head"
                )
            key = PromptKey.AUDIT_MANDATE
            prompt = self._prompts.template_for(key).render(
                {
                    "defect_class": request.defect_class,
                    "refutation_evidence": request.claim.judgment.evidence,
                    "head_sha": request.claim.head_sha,
                    "audited_surfaces": json.dumps(
                        [
                            {
                                "index": index,
                                "tracker_key": item.surface.ref.key,
                                "artifact": item.model_dump(mode="json"),
                            }
                            for index, item in enumerate(covered)
                        ],
                        ensure_ascii=False,
                    ),
                }
            )
            result, limited = await drain(
                self._runner.stream_in_workspace(
                    prompt=prompt,
                    workspace_path=workspace,
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=list(EVAL_TOOLS),
                    skills=self._prompts.session_skills(key, self._skills),
                    session_type=SessionType.SCHEDULED_PASS,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(key),
                    session_id=None,
                    output_format={
                        "type": "json_schema",
                        "schema": AUDIT_MANDATE_SCHEMA,
                    },
                ),
                site="audit_mandate",
            )
            if (
                result is None
                or result.structured_output is None
                or result.is_error
                or limited
            ):
                raise soft_failure(
                    "Mandate hunt produced no structured judgment.",
                    raise_site="audit_mandate",
                    result_event=result,
                    rate_limit_rejected=limited,
                )
            judgment = AuditMandateJudgment.model_validate(result.structured_output)
            if await read_replace_refs(git=self._git, workspace=workspace):
                raise AuditClaimReadError(
                    "the mandate repository substitutes Git objects"
                )
            if await read_workspace_head(git=self._git, workspace=workspace) != (
                request.claim.head_sha,
                False,
            ):
                raise AuditClaimReadError(
                    "mandate workspace changed during verification"
                )
            latest, unreadable = await self._read_set(request.surfaces)
            if latest != covered or unreadable:
                raise AuditClaimReadError(
                    "mandating surface set changed during verification"
                )
            finding_surface = None
            if judgment.source_index is not None:
                if judgment.source_index >= len(covered):
                    raise AuditClaimReadError(
                        "mandate judgment names an unprovided surface"
                    )
                source = covered[judgment.source_index]
                if judgment.verdict is AuditVerdict.HOLDS:
                    finding = judgment.finding
                    if finding is None or finding.mandate_text is None:
                        raise AuditClaimReadError("mandate finding is missing")
                    if (
                        finding.issue_id != source.surface.ref.key
                        or finding.defect_class != request.defect_class
                    ):
                        raise AuditClaimReadError(
                            "mandate finding identity differs from the source or defect"
                        )
                    if finding.mandate_text not in source.content:
                        raise AuditClaimReadError(
                            "mandate quotation is not exact source text"
                        )
                    finding_surface = source.surface
                else:
                    raise AuditClaimReadError(
                        "session claims a successfully read source was unreadable"
                    )
            return AuditClaimReport(
                claim=request.claim,
                mandate=AuditMandateObservation(
                    verdict=judgment.verdict,
                    covered=covered,
                    unreadable=unreadable,
                    finding=judgment.finding,
                    finding_surface=finding_surface,
                    evidence=judgment.evidence,
                ),
            )
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError
