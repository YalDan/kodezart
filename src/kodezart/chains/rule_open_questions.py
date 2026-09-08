"""Native source collection and a fresh read-only fire-time ruling proposal."""

import asyncio
import json
import re

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    RepoCache,
    TrackerCriteriaValidator,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.criteria_feasibility import grounded_finding
from kodezart.domain.errors import RulingProposalError
from kodezart.domain.issue_tree import index_issue_tree
from kodezart.services.git_observations import read_replace_refs, read_workspace_head
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.agent import (
    RULING_PROPOSAL_SCHEMA,
    Ruling,
    RulingAuthor,
    RulingOutput,
    RulingProposalOutput,
)
from kodezart.types.domain.criteria import CriterionVerdict
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from kodezart.types.domain.tracker_feasibility import (
    TrackerFeasibilityObservation,
    TrackerFeasibilityRequest,
)


def _require_commit(request: TrackerFeasibilityRequest) -> None:
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", request.head_sha) is None:
        raise RulingProposalError("a ruling session requires a complete commit SHA")


class TrackerRulingProposer:
    """Observe native sources and propose answers without publishing or admitting.

    The caller resolves the dispatch head. An actual fresh feasibility read
    precedes this session; its source, child family and head must still agree.
    A returned RulingOutput has canonical identities but is not tracker evidence.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        validator: TrackerCriteriaValidator,
        cache: RepoCache,
        git: GitService,
        workspace: WorkspaceProvider,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._tracker = tracker
        self._validator = validator
        self._cache = cache
        self._git = git
        self._workspace = workspace
        self._runner = runner
        self._prompts = prompts
        self._skills = skills

    async def _require_head(self, workspace: str, head: str) -> None:
        if await read_replace_refs(git=self._git, workspace=workspace):
            raise RulingProposalError("the ruling repository substitutes Git objects")
        if await read_workspace_head(git=self._git, workspace=workspace) != (
            head,
            False,
        ):
            raise RulingProposalError("the ruling workspace is not clean at its base")

    async def _sources(
        self, observation: TrackerFeasibilityObservation
    ) -> dict[str, TrackerIssue]:
        subject = observation.spec.subject
        if not await self._tracker.execution_approved(issue_key=subject):
            raise RulingProposalError("execution approval is no longer present")
        criteria = tuple(await self._tracker.read_criteria(issue_key=subject))
        if criteria != observation.criteria:
            raise RulingProposalError("the criterion family changed after feasibility")
        ref = ScopeRef(kind=ScopeKind.ISSUE, key=subject)
        rows = index_issue_tree(
            root=subject, rows=await self._tracker.scope_issues(ref=ref), ref=ref
        )
        if (
            rows[subject].body != observation.spec.body
            or rows[subject].updated_at.isoformat() != observation.spec.read_at_version
            or any(rows.get(row.issue_key) != row for row in criteria)
        ):
            raise RulingProposalError(
                "the native subtree differs from the fire sources"
            )
        return rows

    async def propose(self, request: TrackerFeasibilityRequest) -> RulingOutput:
        """Return only coherent proposals; neither writes nor advances the fire."""
        _require_commit(request)
        observation = await self._validator.validate(request)
        return await self.propose_validated(request, observation)

    async def propose_validated(
        self,
        request: TrackerFeasibilityRequest,
        observation: TrackerFeasibilityObservation,
    ) -> RulingOutput:
        """Retain the prepared source; rereads check coherence, never replace it."""
        _require_commit(request)
        if (
            observation.spec.subject != request.issue_key
            or observation.head_sha != request.head_sha
        ):
            raise RulingProposalError("feasibility names a different fire or base")
        selected = {
            row.issue_key
            for row in observation.criteria
            if row.state_kind is WorkflowStateKind.UNSTARTED
        }
        judgment = observation.judgment
        findings = () if judgment is None else tuple(judgment.findings)
        if (
            not observation.criteria
            or tuple(row.issue_key for row in observation.criteria)
            != observation.spec.criteria
            or len({row.criterion_id for row in findings}) != len(findings)
            or {row.criterion_id for row in findings} != selected
            or (judgment is not None and judgment.contradictions)
            or any(
                grounded_finding(row).verdict is not CriterionVerdict.feasible
                for row in findings
            )
        ):
            raise RulingProposalError(
                "the fire has no complete feasible entry judgment"
            )
        sources = await self._sources(observation)
        key = PromptKey.FIRE_TIME_RULING
        prompt = self._prompts.template_for(key).render(
            {
                "issue_key": request.issue_key,
                "issue_body": observation.spec.body,
                "criteria": json.dumps(
                    [
                        row.model_dump(mode="json", by_alias=True)
                        for issue_key, row in sorted(sources.items())
                        if issue_key != request.issue_key
                    ],
                    ensure_ascii=False,
                ),
                "base_ref": request.head_sha,
                "validation_findings": "null"
                if judgment is None
                else judgment.model_dump_json(by_alias=True),
            }
        )
        repository = await ensure_repository(
            cache=self._cache, repo_url=request.repo_url, cache_key=request.cache_key
        )
        workspace, cancelled = await finish_owned(
            asyncio.create_task(
                self._workspace.acquire(
                    repo_path=repository, ref=request.head_sha, create_branch=False
                )
            )
        )
        try:
            if cancelled:
                raise asyncio.CancelledError
            await self._require_head(workspace, request.head_sha)
            result, rate_limited = await drain(
                self._runner.stream_in_workspace(
                    prompt=prompt,
                    workspace_path=workspace,
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=list(EVAL_TOOLS),
                    skills=self._prompts.session_skills(key, self._skills),
                    session_type=SessionType.TICKET_FIRE,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(key),
                    session_id=None,
                    run_identity=request.run_identity,
                    output_format={
                        "type": "json_schema",
                        "schema": RULING_PROPOSAL_SCHEMA,
                    },
                ),
                site="fire_time_ruling",
            )
            if (
                result is None
                or result.structured_output is None
                or result.is_error
                or rate_limited
            ):
                raise soft_failure(
                    "Fire-time ruling produced no structured proposals.",
                    raise_site="fire_time_ruling",
                    result_event=result,
                    rate_limit_rejected=rate_limited,
                )
            output = RulingProposalOutput.model_validate(result.structured_output)
            if output.unresolved_questions:
                raise RulingProposalError(
                    "unresolved questions require a recorded decision: "
                    + "; ".join(output.unresolved_questions)
                )
            addresses = [(row.issue_ref, row.question) for row in output.rulings]
            if len(set(addresses)) != len(addresses) or any(
                issue not in sources for issue, _ in addresses
            ):
                raise RulingProposalError(
                    "proposals have duplicate or foreign questions"
                )
            if await self._sources(observation) != sources:
                raise RulingProposalError(
                    "the native issue subtree changed during ruling"
                )
            await self._require_head(workspace, request.head_sha)
            return RulingOutput(
                rulings=[
                    Ruling(
                        ruling_id=mint_ruling_id(
                            issue_ref=row.issue_ref, question=row.question
                        ),
                        issue_ref=row.issue_ref,
                        question=row.question,
                        ruling_class=row.ruling_class,
                        resolution=row.resolution,
                        rejected_alternative=row.rejected_alternative,
                        repo_evidence=row.repo_evidence,
                        authored_by=RulingAuthor.MACHINE,
                    )
                    for row in output.rulings
                ]
            )
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError
