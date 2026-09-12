"""Tracker-backed admission sessions, independent on every assess and verify call."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.errors import OrganizeAdmissionIdentityError
from kodezart.domain.organize import is_admission_live
from kodezart.domain.prompt_variables import organize_variables
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.agent import ORGANIZE_ADMISSION_SCHEMA, RaiseSite
from kodezart.types.domain.organize import (
    AdmissionJudgment,
    AdmissionResult,
    OrganizeAdmissionRequest,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


class OrganizeAdmission:
    """Read source evidence and return an admission judgment without tracker writes.

    This boundary owns the fresh session and selected-base workspace. It has
    no author transcript or previous session parameter. Each call re-reads
    tracker sources, including verify after an author has changed the issue.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._tracker = tracker
        self._runner = runner
        self._workspace = workspace
        self._prompts = prompts
        self._skills = skills
        self._log: BoundLogger = get_logger(__name__)

    async def assess(self, request: OrganizeAdmissionRequest) -> AdmissionResult:
        """Assess the current tracker source in a fresh, read-only session."""
        return await self._judge(
            request, key=request.admission_prompt_key, site="organize_assess"
        )

    async def verify(self, request: OrganizeAdmissionRequest) -> AdmissionResult:
        """Verify current sources independently of any author session."""
        return await self._judge(
            request, key=PromptKey.ORGANIZE_VERIFY, site="organize_verify"
        )

    async def is_live(self, result: AdmissionResult) -> bool:
        """Read current revision without dispatching a session or restamping it."""
        revision = await self._tracker.read_issue_revision(issue_key=result.issue_id)
        if revision.issue.issue_key != result.issue_id:
            raise OrganizeAdmissionIdentityError(
                expected=result.issue_id, observed=revision.issue.issue_key
            )
        return is_admission_live(
            admitted_body_digest=result.admitted_body_digest,
            current_body_digest=revision.body_digest,
        )

    async def _judge(
        self,
        request: OrganizeAdmissionRequest,
        *,
        key: PromptKey,
        site: RaiseSite,
    ) -> AdmissionResult:
        revision = await self._tracker.read_issue_revision(issue_key=request.issue_key)
        subject = revision.issue
        if subject.issue_key != request.issue_key:
            raise OrganizeAdmissionIdentityError(
                expected=request.issue_key, observed=subject.issue_key
            )
        linked_keys = sorted(
            {relation.issue_key for relation in subject.relations} - {subject.issue_key}
        )
        linked = [
            await self._tracker.read_issue(issue_key=issue_key)
            for issue_key in linked_keys
        ]
        criteria = await self._tracker.read_criteria(issue_key=subject.issue_key)
        prompt = self._prompts.template_for(key).render(
            {
                **organize_variables(
                    mandate_rubric=request.mandate_rubric,
                    issue_body=subject.body,
                    linked_issue_bodies=[issue.body for issue in linked],
                    criterion_issue_bodies=[issue.body for issue in criteria],
                    refusal_evidence=None,
                    defect_classes=request.defect_classes,
                ),
                "issue_key": subject.issue_key,
                "base_ref": request.base_ref,
            }
        )
        async with owned_workspace(
            self._workspace,
            repo_url=request.repo_url,
            ref=request.base_ref,
            cache_key=request.cache_key,
        ) as workspace:
            await self._log.ainfo(
                "organize_admission_attempt",
                issue_key=subject.issue_key,
                prompt_key=key.value,
                base_ref=request.base_ref,
                session_id=None,
                resumed_session_id=None,
            )
            structured = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=workspace,
                key=key,
                prompt=prompt,
                output_schema=ORGANIZE_ADMISSION_SCHEMA,
                site=site,
                session_type=SessionType.ORGANIZE_PASS,
                failure_message="Organize admission produced no structured output.",
            )
            judgment = AdmissionJudgment.model_validate(structured)
            if judgment.issue_id != subject.issue_key:
                raise OrganizeAdmissionIdentityError(
                    expected=subject.issue_key, observed=judgment.issue_id
                )
            return AdmissionResult.model_validate(
                {**judgment.model_dump(), "admitted_body_digest": revision.body_digest}
            )
