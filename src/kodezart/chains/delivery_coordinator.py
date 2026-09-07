"""Delivery check classification from declarations and same-commit evidence.

The common coordinator owns PR creation and check watching. Residual
publication and remediation remain explicit unfinished routes. No
classification reads summary or log text; summaries are carried as evidence.
"""

import asyncio

from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import soft_failure
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    CIMonitor,
    ForgeQuery,
    GitService,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
    RepoCache,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import (
    BaseResolutionError,
    DeliveryContextError,
    DeliveryRouteUnavailableError,
)
from kodezart.domain.pr_body import append_flagged_section, append_tracker_issue
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import PR_DESCRIPTION_SCHEMA, PRDescriptionOutput
from kodezart.types.domain.delivery import (
    CheckRedClass,
    CheckRedObservation,
    DeliveryContext,
    LaneDelivery,
    LaneDispatch,
)
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import RepoEntry, RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


class DeliveryCoordinator:
    """Open a lane's PR and report its observed check result.

    Scope-walker dispatch and fire graph extraction are separate consumers.
    Until residual publication and remediation are connected, an observation
    requiring either raises with its PR facts instead of returning success.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        pr_creator: PRCreator,
        forge_query: ForgeQuery,
        ci: CIMonitor,
        git: GitService,
        cache: RepoCache,
        git_remote: str,
        config: AppConfig,
        artifact_persister: ArtifactPersister | None,
    ) -> None:
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._gate = gate
        self._pr_creator = pr_creator
        self._forge_query = forge_query
        self._ci = ci
        self._git = git
        self._cache = cache
        self._git_remote = git_remote
        self._watch_slots = asyncio.Semaphore(config.delivery_max_concurrent_watches)
        self._artifact_persister = artifact_persister
        self._log = get_logger(__name__)

    async def deliver(
        self,
        dispatch: LaneDispatch,
        *,
        feature_branch: str,
        final_commit_sha: str,
        context: DeliveryContext,
    ) -> LaneDelivery:
        """Deliver the supplied fire's head against its recorded base."""
        execution = context.execution
        identity = execution.run_identity
        if (
            identity is None
            or identity.kind is not RunKind.FIRE
            or identity.name != dispatch.issue_id
        ):
            raise DeliveryContextError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="delivery requires the dispatched issue's FIRE run identity",
            )
        if (
            feature_branch != dispatch.head_branch
            or execution.base_spec != dispatch.resolved_base
            or not final_commit_sha.strip()
            or not execution.repo_url
        ):
            raise DeliveryContextError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="head, resolved base, final SHA and repository must agree",
            )
        if context.fire_outcome is not WorkflowOutcome.handed_off_for_delivery:
            raise DeliveryRouteUnavailableError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="this fire outcome has no connected delivery route",
                pr_url=None,
                pr_number=None,
                checks_passed=None,
                checks_summary=None,
            )
        existing = await self._forge_query.open_pr_for_head(
            repo_url=execution.repo_url, head=feature_branch
        )
        if existing is not None:
            url, number = existing
            raise DeliveryRouteUnavailableError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="existing PR requires an unconnected content-edit route",
                pr_url=url,
                pr_number=number,
                checks_passed=None,
                checks_summary=None,
            )
        remote_sha = await self._require_remote_branches(dispatch, context)
        if remote_sha != final_commit_sha:
            raise DeliveryContextError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="remote head differs from the fire's final commit SHA",
            )
        if self._artifact_persister is not None:
            await self._artifact_persister.clean(
                repo_path=execution.repo_path,
                repo_url=execution.repo_url,
                branch=feature_branch,
                cache_key=execution.cache_key,
            )
            # Cleaning may commit and push. Preserve the fire's SHA, and
            # re-read both remote refs instead of treating it as the new tip.
            await self._require_remote_branches(dispatch, context)
        description = await self._description(context, feature_branch=feature_branch)
        body = append_tracker_issue(
            append_flagged_section(description.description, context.flagged_items),
            dispatch.issue_id,
        )
        title = await self._gated(
            description.title, context.visibility, OutboundDestination.PR_TITLE
        )
        body = await self._gated(body, context.visibility, OutboundDestination.PR_BODY)
        url, number = await self._pr_creator.create_pr(
            repo_url=execution.repo_url,
            title=title,
            body=body,
            head=feature_branch,
            base=dispatch.resolved_base.base_branch,
        )
        async with self._watch_slots:
            passed, summary = await self._ci.wait_for_checks(
                repo_url=execution.repo_url, ref=feature_branch
            )
        if passed is True:
            outcome = WorkflowOutcome.ci_passed
        elif passed is None and not await self._ci.checks_declared(
            repo_url=execution.repo_url
        ):
            outcome = WorkflowOutcome.ci_not_configured
        else:
            raise DeliveryRouteUnavailableError(
                lane_key=dispatch.lane_key,
                issue_id=dispatch.issue_id,
                reason="check observation requires an unconnected delivery route",
                pr_url=url,
                pr_number=number,
                checks_passed=passed,
                checks_summary=summary,
            )
        return LaneDelivery(
            lane_key=dispatch.lane_key,
            issue_id=dispatch.issue_id,
            head_branch=feature_branch,
            base_branch=dispatch.resolved_base.base_branch,
            pr=LanePR(url=url, number=number, state="open"),
            checks_passed=passed,
            checks_summary=summary,
            outcome=outcome,
        )

    async def _require_remote_branches(
        self, dispatch: LaneDispatch, context: DeliveryContext
    ) -> str:
        execution = context.execution
        cwd = execution.repo_path
        if cwd is None:
            if execution.repo_url is None:
                raise DeliveryContextError(
                    lane_key=dispatch.lane_key,
                    issue_id=dispatch.issue_id,
                    reason="remote branch lookup requires a repository",
                )
            cwd = await self._cache.ensure_available(
                execution.repo_url, execution.cache_key
            )
        head = await self._git.remote_branch_sha(
            cwd=cwd, remote=self._git_remote, branch=dispatch.head_branch
        )
        base = await self._git.remote_branch_sha(
            cwd=cwd,
            remote=self._git_remote,
            branch=dispatch.resolved_base.base_branch,
        )
        if head is None or base is None:
            raise BaseResolutionError(
                "delivery requires both recorded branches on the remote",
                issue_id=dispatch.issue_id,
                blocker_issue_ids=[
                    item.blocker_issue_id for item in dispatch.resolved_base.inputs
                ],
                branches=[
                    branch
                    for branch, sha in (
                        (dispatch.head_branch, head),
                        (dispatch.resolved_base.base_branch, base),
                    )
                    if sha is None
                ],
            )
        return head

    async def _description(
        self, context: DeliveryContext, *, feature_branch: str
    ) -> PRDescriptionOutput:
        execution = context.execution
        key = PromptKey.PR_DESCRIPTION
        prompt = self._prompts.template_for(key).render(
            {
                "task_md": format_ticket_as_task(context.ticket),
                "acceptance_criteria": list(context.criteria),
                "total_iterations": context.total_iterations,
            }
        )
        result, rate_limit_rejected = await drain(
            self._runner.stream(
                prompt=prompt,
                repo_path=execution.repo_path,
                repo_url=execution.repo_url,
                branch=feature_branch,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=[],
                skills=self._prompts.session_skills(key, self._skills),
                session_type=SessionType.TICKET_FIRE,
                run_identity=execution.run_identity,
                agents=NO_SUBAGENTS,
                session_policy=self._prompts.session_policy(key),
                session_id=None,
                output_format={"type": "json_schema", "schema": PR_DESCRIPTION_SCHEMA},
                cache_key=execution.cache_key,
            ),
            site="pr_description",
        )
        if (
            result is None
            or result.structured_output is None
            or result.is_error
            or rate_limit_rejected
        ):
            raise soft_failure(
                "Agent did not produce a successful PR description",
                raise_site="pr_description",
                result_event=result,
                rate_limit_rejected=rate_limit_rejected,
            )
        return PRDescriptionOutput.model_validate(result.structured_output)

    async def _gated(
        self, content: str, visibility: RepoVisibility, destination: OutboundDestination
    ) -> str:
        return await gated_write(
            gate=self._gate,
            log=self._log,
            content=content,
            visibility=visibility,
            shape=WriterShape.PROSE,
            destination=destination,
            content_class=ContentClass.AUTHORED,
        )


async def classify_red_checks(
    *,
    ci: CIMonitor,
    repository: RepoEntry,
    final_commit_sha: str,
    initial_summary: str,
    config: AppConfig,
) -> CheckRedObservation:
    """Classify an already-observed red, preserving the immutable commit.

    An explicitly unmet prerequisite wins before a rerun is consumed.
    Otherwise every observation contributes: a later return to the original
    failing set cannot erase an earlier differing red set.
    """
    original = await ci.failed_check_names(
        repo_url=repository.url, ref=final_commit_sha
    )
    if not original:
        raise ValueError("a red observation must identify a failing check")
    unmet = any(
        repository.runner_environment.get(prerequisite) is False
        for step in repository.checks
        if step.forge_check in original
        for prerequisite in step.requires
    )
    if unmet:
        return CheckRedObservation(
            red_class=CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
            checks_passed=False,
            checks_summary=initial_summary,
        )
    reproduced = True
    summary = initial_summary
    for _ in range(config.delivery_red_rerun_max_attempts):
        await ci.rerun_checks(repo_url=repository.url, ref=final_commit_sha)
        passed, summary = await ci.wait_for_checks(
            repo_url=repository.url, ref=final_commit_sha
        )
        if passed is not False:
            return CheckRedObservation(
                red_class=CheckRedClass.RUNNER_FLAKE,
                checks_passed=passed,
                checks_summary=summary,
            )
        observed = await ci.failed_check_names(
            repo_url=repository.url, ref=final_commit_sha
        )
        if not observed:
            raise ValueError("a red re-observation must identify a failing check")
        reproduced = reproduced and observed == original
    return CheckRedObservation(
        red_class=CheckRedClass.WORK_DEFECT
        if reproduced
        else CheckRedClass.UNCLASSIFIED,
        checks_passed=False,
        checks_summary=summary,
    )
