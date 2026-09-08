"""Read-only pre-loop feasibility over the tracker's current criterion family."""

import asyncio

from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.logging import get_logger
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    RepoCache,
    TrackerPort,
    WorkspaceProvider,
)
from kodezart.core.redispatch import (
    correction_notice,
    correction_report,
    until_conforming,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.criteria_feasibility import (
    grounded_finding,
    minimal_conflicting_subsets,
    reconcile_findings,
)
from kodezart.domain.errors import (
    CriteriaFanInError,
    TrackerFeasibilityReadError,
    UngroundedVerdictError,
)
from kodezart.domain.fire_spec import criterion_check
from kodezart.domain.ticket import format_fire_spec
from kodezart.services.git_observations import read_workspace_head
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.agent import TRACKER_CRITERIA_VALIDATION_SCHEMA
from kodezart.types.domain.criteria import TrackerCriteriaValidationOutput
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from kodezart.types.domain.tracker_feasibility import (
    TrackerFeasibilityObservation,
    TrackerFeasibilityRequest,
)


def _require_family(spec: TrackerSpec, criteria: tuple[TrackerIssue, ...]) -> None:
    keys = tuple(row.issue_key for row in criteria)
    if (
        keys != spec.criteria
        or len(set(keys)) != len(keys)
        or any(
            row.parent_key != spec.subject or "criterion" not in row.issue_labels
            for row in criteria
        )
    ):
        raise TrackerFeasibilityReadError(
            "the current criterion family differs from the captured tracker spec"
        )


def _reconcile(
    criteria: tuple[TrackerIssue, ...], output: TrackerCriteriaValidationOutput
) -> TrackerCriteriaValidationOutput:
    findings = reconcile_findings(
        dispatched=tuple(row.issue_key for row in criteria),
        findings=output.findings,
        contradictions=output.contradictions,
    )
    for finding in findings:
        grounded_finding(finding)
    return TrackerCriteriaValidationOutput(
        findings=list(findings),
        contradictions=list(minimal_conflicting_subsets(output.contradictions)),
    )


class TrackerFeasibilityValidator:
    """Collect, dispatch and ground a fresh judgment without authoring state.

    The caller resolves the dispatch head before invoking this consumer. It
    supplies no replacement subject, criterion body or prior-session context.
    Applying amendments and authorizing the fire remain separate operations.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        cache: RepoCache,
        git: GitService,
        workspace: WorkspaceProvider,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._cache = cache
        self._git = git
        self._workspace = workspace
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._attempts = config.fan_in_max_attempts
        self._log = get_logger(__name__)

    async def _require_head(self, workspace: str, head: str) -> None:
        replacements, cancelled = await finish_owned(
            asyncio.create_task(self._git.has_replace_refs(workspace))
        )
        if cancelled:
            raise asyncio.CancelledError
        if replacements:
            raise TrackerFeasibilityReadError(
                "the verification repository has replacement Git objects"
            )
        observed, dirty = await read_workspace_head(git=self._git, workspace=workspace)
        if observed != head:
            raise TrackerFeasibilityReadError(
                "the workspace moved from the dispatch head"
            )
        if dirty:
            raise TrackerFeasibilityReadError(
                "the verification workspace contains changes"
            )

    async def validate(
        self, request: TrackerFeasibilityRequest
    ) -> TrackerFeasibilityObservation:
        """Re-validate Todo children, retaining the exact source and dispatch head."""
        spec = await self._tracker.read_fire_spec(issue_key=request.issue_key)
        if spec.subject != request.issue_key:
            raise TrackerFeasibilityReadError("the spec names a different subject")
        criteria = tuple(await self._tracker.read_criteria(issue_key=request.issue_key))
        _require_family(spec, criteria)
        selected = tuple(
            row for row in criteria if row.state_kind is WorkflowStateKind.UNSTARTED
        )
        key = PromptKey.CRITERIA_VALIDATION
        prompt = self._prompts.template_for(key).render(
            {
                "task_description": format_fire_spec(spec),
                "acceptance_criteria": [
                    {
                        "id": row.issue_key,
                        "text": criterion_check(
                            criterion=row, issue_key=request.issue_key
                        ),
                    }
                    for row in selected
                ],
                "base_ref": request.head_sha,
                "tracker_criteria": True,
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
            judgment = None
            correction = None
            if selected:

                async def dispatch(
                    breach: Exception | None,
                ) -> TrackerCriteriaValidationOutput:
                    await self._require_head(workspace, request.head_sha)
                    notice = None if breach is None else correction_notice(breach)
                    result, rate_limited = await drain(
                        self._runner.stream_in_workspace(
                            prompt=prompt
                            if notice is None
                            else f"{prompt}\n\n{notice}",
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
                                "schema": TRACKER_CRITERIA_VALIDATION_SCHEMA,
                            },
                        ),
                        site="criteria_validation",
                    )
                    if (
                        result is None
                        or result.structured_output is None
                        or result.is_error
                        or rate_limited
                    ):
                        raise soft_failure(
                            "Tracker feasibility produced no structured judgment.",
                            raise_site="criteria_validation",
                            result_event=result,
                            rate_limit_rejected=rate_limited,
                        )
                    return TrackerCriteriaValidationOutput.model_validate(
                        result.structured_output
                    )

                retried = await until_conforming(
                    dispatch=dispatch,
                    check=lambda output: _reconcile(selected, output),
                    correctable=(
                        ValidationError,
                        CriteriaFanInError,
                        UngroundedVerdictError,
                    ),
                    max_attempts=self._attempts,
                    site="criteria_validation",
                    log=self._log,
                )
                if retried.unresolved is not None:
                    raise retried.unresolved
                judgment = _reconcile(selected, retried.output)
                correction = correction_report(retried)
            latest = tuple(
                await self._tracker.read_criteria(issue_key=request.issue_key)
            )
            if latest != criteria:
                raise TrackerFeasibilityReadError(
                    "the criterion family changed during validation"
                )
            await self._require_head(workspace, request.head_sha)
            return TrackerFeasibilityObservation(
                spec=spec,
                criteria=criteria,
                head_sha=request.head_sha,
                judgment=judgment,
                derivations=()
                if judgment is None
                else tuple(grounded_finding(f) for f in judgment.findings),
                correction=correction,
            )
        finally:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._workspace.release(workspace))
            )
            if cancelled:
                raise asyncio.CancelledError
