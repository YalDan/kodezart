"""Read-only proposals; the Organize owner alone applies tracker writes."""

from dataclasses import dataclass

from kodezart.core.protocols import (
    AgentRunner,
    PromptSetProvider,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.domain.errors import OrganizeAdmissionIdentityError
from kodezart.domain.prompt_variables import organize_variables
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.organize_context import OrganizeContextReader
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.agent import ORGANIZE_PROPOSAL_SCHEMA
from kodezart.types.domain.organize import OrganizeAdmissionRequest
from kodezart.types.domain.organize_graph import OrganizeContext
from kodezart.types.domain.organize_owner import OrganizeProposal
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.tracker import TrackerIssueRevision


@dataclass(frozen=True)
class ProposedWrite:
    context: OrganizeContext
    revision: TrackerIssueRevision
    proposal: OrganizeProposal


class OrganizeAuthor:
    def __init__(
        self,
        *,
        tracker: TrackerPort,
        context: OrganizeContextReader,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._context = context
        self._tracker, self._runner, self._workspace = tracker, runner, workspace
        self._prompts, self._skills = prompts, skills

    async def propose(
        self, request: OrganizeAdmissionRequest, *, key: PromptKey, evidence: str | None
    ) -> ProposedWrite:
        context = await self._context.read(scope=request.scope)
        revision = await self._tracker.read_issue_revision(issue_key=request.issue_key)
        subject = revision.issue
        if subject.issue_key != request.issue_key:
            raise OrganizeAdmissionIdentityError(
                expected=request.issue_key, observed=subject.issue_key
            )
        self._context.require_revision(context, revision)
        linked_keys = {relation.issue_key for relation in subject.relations} - {
            subject.issue_key
        }
        linked = [issue for issue in context.issues if issue.issue_key in linked_keys]
        criteria = await self._tracker.read_criteria(issue_key=subject.issue_key)
        prompt = self._prompts.template_for(key).render(
            {
                **organize_variables(
                    graph_context=context.model_dump_json(),
                    mandate_rubric=request.mandate_rubric,
                    issue_body=subject.body,
                    linked_issue_bodies=[i.body for i in linked],
                    criterion_issue_bodies=[i.body for i in criteria],
                    refusal_evidence=None,
                    defect_classes=request.defect_classes,
                ),
                "refusal_evidence": evidence,
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
            structured = await judge_in_workspace(
                runner=self._runner,
                prompts=self._prompts,
                skills=self._skills,
                workspace=workspace,
                key=key,
                prompt=prompt,
                output_schema=ORGANIZE_PROPOSAL_SCHEMA,
                site=(
                    "organize_criteria_author"
                    if key is PromptKey.ORGANIZE_CRITERIA_AUTHOR
                    else "organize_author"
                ),
                session_type=SessionType.ORGANIZE_PASS,
                failure_message="Organize author produced no structured proposal.",
            )
        proposal = OrganizeProposal.model_validate(structured)
        if proposal.root.issue_id != subject.issue_key:
            raise OrganizeAdmissionIdentityError(
                expected=subject.issue_key, observed=proposal.root.issue_id
            )
        return ProposedWrite(context=context, revision=revision, proposal=proposal)
