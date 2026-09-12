"""Open a native lane's pull request and observe its checks without merging."""

import asyncio
from collections.abc import Sequence

from kodezart.chains.criteria import require_current_native_snapshot
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import soft_failure
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    AgentRunner,
    CIMonitor,
    FireCriteriaReader,
    ForgeQuery,
    GitService,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import (
    BaseResolutionError,
    CheckObservationError,
    DeliveryHeadError,
    ForgeAPIError,
    TransientAPIError,
)
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.pr_body import (
    append_flagged_section,
    append_tracker_issue,
    require_tracker_issue,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.domain.workflow_state import original_fire_spec, validated_criteria
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.agent import PR_DESCRIPTION_SCHEMA, PRDescriptionOutput
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    IncompleteChecks,
    ObservedChecks,
)
from kodezart.types.domain.delivery import (
    CheckRedClass,
    LaneDelivery,
    classify_lane_delivery,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import ContentClass, OutboundDestination, WriterShape
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_state import LanePR
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState


class LaneDeliveryCoordinator:
    """The lane's existing head/base, open PR and coherent check observation.

    Capabilities are narrow: no tracker port and no PR merge/state reader.
    NativeLaneWorkflow owns the existing fire remediation transition.
    """

    def __init__(
        self,
        *,
        service: AgentRunner,
        git: GitService,
        pr_creator: PRCreator,
        forge_query: ForgeQuery,
        ci: CIMonitor,
        criteria_reader: FireCriteriaReader,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        repositories: Sequence[RepoEntry],
        git_base_url: str,
        max_concurrent_watches: int,
        red_rerun_max_attempts: int,
    ) -> None:
        if max_concurrent_watches < 1 or red_rerun_max_attempts < 0:
            raise ValueError(
                "delivery needs a positive watch bound and nonnegative rerun bound"
            )
        self._criteria_reader = criteria_reader
        self._service, self._git = service, git
        self._pr_creator, self._forge_query, self._ci = pr_creator, forge_query, ci
        self._prompts, self._skills, self._gate = prompts, skills, gate
        self._repositories = tuple(repo.model_copy(deep=True) for repo in repositories)
        self._git_base_url = git_base_url
        self._red_rerun_max_attempts = red_rerun_max_attempts
        self._watch_slots = asyncio.Semaphore(max_concurrent_watches)
        self._log = get_logger(__name__)

    async def deliver(
        self,
        *,
        state: WorkflowState,
        context: ExecutionContext,
        stalled: bool,
        remediation_available: bool,
    ) -> LaneDelivery:
        """Open or reuse the lane PR, then observe and classify its actual head."""
        issue_id = state["issue_key"]
        repo_url = context.repo_url
        head = state["feature_branch"]
        sha = state["feature_tip_sha"]
        if issue_id is None or repo_url is None or sha is None:
            raise ValueError(
                "Native delivery requires its issue, repository and published SHA"
            )
        spec = original_fire_spec(state)
        if not isinstance(spec, TrackerSpec) or spec.subject != issue_id:
            raise ValueError("Native delivery must preserve its captured subject")
        await require_current_native_snapshot(state, reader=self._criteria_reader)
        canonical = resolve_repo_url(repo_url, self._git_base_url)
        matches = tuple(
            repo
            for repo in self._repositories
            if resolve_repo_url(repo.url, self._git_base_url) == canonical
        )
        if len(matches) > 1:
            raise ValueError("delivery repository declarations are ambiguous")
        repository = matches[0] if matches else None
        cwd = context.repo_path or "."
        remote_sha = await self._git.remote_branch_sha(cwd, repo_url, head)
        if remote_sha != sha:
            raise DeliveryHeadError(
                issue_id=issue_id,
                branch=head,
                expected_sha=sha,
                observed_sha=remote_sha,
            )
        base = context.base_branch
        if await self._git.remote_branch_sha(cwd, repo_url, base) is None:
            raise BaseResolutionError(
                "The dispatch-resolved base is absent from the remote",
                issue_id=issue_id,
                branches=(base,),
                blocker_issue_ids=tuple(
                    item.blocker_issue_id for item in context.base_spec.inputs
                ),
            )
        existing = await self._forge_query.open_pr_for_head(
            repo_url=repo_url, head=head
        )
        if existing is None:
            pr = await self._open_pr(state, context)
        else:
            pr = LanePR(url=existing[0], number=existing[1], state="open")
        red_class = None
        async with self._watch_slots:
            observed = await self._ci.wait_for_checks(repo_url=repo_url, ref=head)
            if isinstance(observed, IncompleteChecks):
                raise CheckObservationError(
                    repo_url=repo_url, ref=head, reason=observed.summary
                )
            if isinstance(observed, ObservedChecks):
                if observed.commit_sha != sha:
                    raise CheckObservationError(
                        repo_url=repo_url,
                        ref=head,
                        reason="Watched checks do not identify the published lane head",
                    )
                if not observed.checks_passed:
                    red = await classify_red_checks(
                        ci=self._ci,
                        repo_url=repo_url,
                        repository=repository,
                        initial=observed,
                        max_attempts=self._red_rerun_max_attempts,
                    )
                    observed, red_class = red.observation, red.red_class
            no_run = (
                isinstance(observed, AbsentChecks)
                and await self._ci.checks_declared(repo_url=repo_url)
                and not (repository is not None and repository.forge_exempt)
            )
        pending = (
            red_class is CheckRedClass.WORK_DEFECT
            and remediation_available
            and not stalled
        )
        result = LaneDelivery(
            lane_key=issue_id,
            issue_id=issue_id,
            head_branch=head,
            base_branch=base,
            final_commit_sha=sha,
            pr=pr,
            observation=observed,
            red_class=red_class,
            no_run_at_ref=no_run,
            stalled=stalled,
            remediation_pending=pending,
            checks_passed=observed.checks_passed
            if isinstance(observed, ObservedChecks)
            else None,
            checks_summary=observed.summary,
            outcome=classify_lane_delivery(
                observation=observed,
                red_class=red_class,
                no_run_at_ref=no_run,
                stalled=stalled,
                remediation_pending=pending,
            ),
        )
        if (
            result.checks_passed is False or result.no_run_at_ref
        ) and not result.remediation_pending:
            body = await gated_write(
                gate=self._gate,
                log=self._log,
                content=(
                    f"## Lane checks: {result.outcome.value}\n\n{result.checks_summary}"
                ),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_COMMENT,
                content_class=ContentClass.AUTHORED,
            )
            try:
                await self._pr_creator.comment_on_pr(
                    repo_url=repo_url, pr_number=pr.number, body=body
                )
            except (ForgeAPIError, TransientAPIError) as exc:
                await self._log.aerror(
                    "comment_failure_failed",
                    error=str(exc),
                    error_kind=type(exc).__name__,
                )
        return result

    async def _open_pr(self, state: WorkflowState, ctx: ExecutionContext) -> LanePR:
        repo_url = ctx.repo_url
        if repo_url is None:
            raise ValueError("Native publication requires its repository")
        prompt = self._prompts.template_for(PromptKey.PR_DESCRIPTION).render(
            {
                "task_md": format_fire_spec(original_fire_spec(state)),
                "acceptance_criteria": validated_criteria(state),
                "total_iterations": state["total_iterations"],
            }
        )
        result, rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=ctx.repo_path,
                repo_url=repo_url,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=[],
                skills=self._prompts.session_skills(
                    PromptKey.PR_DESCRIPTION, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                run_identity=ctx.run_identity,
                session_policy=self._prompts.session_policy(PromptKey.PR_DESCRIPTION),
                output_format={"type": "json_schema", "schema": PR_DESCRIPTION_SCHEMA},
                cache_key=ctx.cache_key,
            ),
            site="pr_description",
        )
        if result is None or result.structured_output is None:
            raise soft_failure(
                "Agent did not produce structured output for PR description",
                raise_site="pr_description",
                result_event=result,
                rate_limit_rejected=rejected,
            )
        output = PRDescriptionOutput.model_validate(result.structured_output)
        title = await gated_write(
            gate=self._gate,
            log=self._log,
            content=output.title,
            visibility=state["repo_visibility"],
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_TITLE,
            content_class=ContentClass.AUTHORED,
        )
        body = require_tracker_issue(
            await gated_write(
                gate=self._gate,
                log=self._log,
                content=append_tracker_issue(
                    append_flagged_section(output.description, state["flagged_items"]),
                    state["issue_key"],
                ),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_BODY,
                content_class=ContentClass.AUTHORED,
            ),
            state["issue_key"],
        )
        await require_current_native_snapshot(state, reader=self._criteria_reader)
        url, number = await self._pr_creator.create_pr(
            repo_url=repo_url,
            title=title,
            body=body,
            head=state["feature_branch"],
            base=ctx.base_branch,
        )
        return LanePR(url=url, number=number, state="open")
