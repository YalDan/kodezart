"""Fresh current-head claim judgments, before full sweep publication."""

from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    RepoCache,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.fire_spec import criterion_check
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA
from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimObservation,
    AuditClaimRequest,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
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
        members = await self._tracker.read_criteria(issue_key=request.lane_issue_key)
        selected = [row for row in members if row.issue_key == request.criterion_key]
        try:
            (criterion,) = selected
        except ValueError as exc:
            raise AuditClaimReadError(
                "the lane has no unique requested criterion"
            ) from exc
        if criterion.parent_key != request.lane_issue_key:
            raise AuditClaimReadError("the criterion no longer belongs to the lane")
        return criterion

    async def verify(self, request: AuditClaimRequest) -> AuditClaimObservation:
        """Judge the current Check at an exact, live remote head in a fresh session."""
        criterion = await self._criterion(request)
        check = criterion_check(criterion=criterion, issue_key=request.lane_issue_key)
        comment, record = await self._records.read(
            issue_key=request.lane_issue_key,
            lane_key=request.lane_key,
            record_ref=request.record_ref,
        )
        repository = await self._cache.ensure_available(
            request.repo_url, request.cache_key
        )
        head = await self._git.remote_branch_sha(
            repository, self._remote, record.branch
        )
        if head is None or not head.strip():
            raise AuditClaimReadError("the recorded branch has no live remote head")
        key = PromptKey.AUDIT_CLAIM
        prompt = self._prompts.template_for(key).render(
            {"criterion_key": criterion.issue_key, "head_sha": head, "check": check}
        )
        workspace = await self._workspace.acquire(
            repo_path=repository,
            ref=head,
            create_branch=False,
        )
        try:
            if await self._git.current_sha(workspace) != head:
                raise AuditClaimReadError(
                    "the audit workspace is not at the selected head"
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
            if (
                await self._git.remote_branch_sha(
                    repository, self._remote, record.branch
                )
                != head
                or await self._git.current_sha(workspace) != head
            ):
                raise AuditClaimReadError(
                    "the branch or workspace moved during verification"
                )
            return AuditClaimObservation(
                judgment=judgment,
                head_sha=head,
                record_ref=comment.comment_key,
                check=check,
            )
        finally:
            await self._workspace.release(workspace)
