"""Authored HTTP delivery around the shared delivery-free fire graph."""

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
)
from kodezart.core.errors import soft_failure
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    BranchMerger,
    CIMonitor,
    GitService,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
    QualityGate,
    RefPublisher,
    Remediator,
    RepoCache,
    RepoVisibilityResolver,
    TicketGenerator,
)
from kodezart.core.retry import DelayFloor
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import (
    gate_cleared,
)
from kodezart.domain.authored_outcome import classify_authored_outcome
from kodezart.domain.ci import ci_status_of
from kodezart.domain.errors import (
    ForgeAPIError,
    TransientAPIError,
)
from kodezart.domain.pr_body import (
    append_flagged_section,
    append_tracker_issue,
    require_tracker_issue,
)
from kodezart.domain.stall_report import stall_pr_body, stall_pr_title
from kodezart.domain.ticket import format_fire_spec
from kodezart.domain.workflow_state import (
    current_ticket,
    original_ticket,
    validated_criteria,
)
from kodezart.types.domain.agent import (
    PR_DESCRIPTION_SCHEMA,
    AgentEvent,
    AuthoredWorkflowCompleteEvent,
    PRDescriptionOutput,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.workflow import (
    AuthoredWorkflowState,
    ExecutionContext,
    RemediationRequest,
)


class AuthoredDeliveryCoordinator(RalphWorkflowEngine):
    """Authored HTTP orchestration around the one shared fire graph."""

    def __init__(
        self,
        service: AgentRunner,
        quality_gate: QualityGate,
        ticket_generator: TicketGenerator,
        merger: BranchMerger,
        git_base_url: str,
        *,
        git_remote: str,
        git: GitService,
        cache: RepoCache,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        visibility_resolver: RepoVisibilityResolver | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int,
        retry_initial_interval: float,
        delay_floor_for: DelayFloor,
        pr_creator: PRCreator | None = None,
        ci_monitor: CIMonitor | None = None,
        ref_publisher: RefPublisher | None = None,
        remediator: Remediator | None = None,
        remediation_max_rounds: int,
        criteria_max_regeneration_rounds: int,
        fan_in_max_attempts: int,
        artifact_persister: ArtifactPersister | None = None,
    ) -> None:
        super().__init__(
            service=service,
            quality_gate=quality_gate,
            ticket_generator=ticket_generator,
            merger=merger,
            git_base_url=git_base_url,
            git_remote=git_remote,
            git=git,
            cache=cache,
            prompts=prompts,
            skills=skills,
            gate=gate,
            visibility_resolver=visibility_resolver,
            checkpointer=checkpointer,
            retry_max_attempts=retry_max_attempts,
            retry_initial_interval=retry_initial_interval,
            delay_floor_for=delay_floor_for,
            ref_publisher=ref_publisher if pr_creator is not None else None,
            remediator=remediator,
            remediation_max_rounds=remediation_max_rounds,
            criteria_max_regeneration_rounds=criteria_max_regeneration_rounds,
            fan_in_max_attempts=fan_in_max_attempts,
            artifact_persister=artifact_persister,
        )
        self._pr_creator = pr_creator
        self._ci_monitor = ci_monitor
        self._delivery_compiled = self._build_delivery_graph().compile(
            checkpointer=self._checkpointer,
        )

    async def run(
        self,
        *,
        prompt: str,
        issue_key: str | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
        run_identity: RunIdentity | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Preserve the authored HTTP stream around the shared fire graph."""
        fire_state, config = self._prepare(
            prompt=prompt,
            issue_key=issue_key,
            repo_path=repo_path,
            repo_url=repo_url,
            base_spec=base_spec,
            scope=scope,
            implied_base=implied_base,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            cache_key=cache_key,
            run_identity=run_identity,
        )
        initial_state = AuthoredWorkflowState(
            **fire_state,
            pr_url=None,
            pr_number=None,
            ci_status=CIStatus.not_monitored,
            ci_summary=None,
        )
        terminal: AuthoredWorkflowCompleteEvent | None = None
        async for event in self._delivery_compiled.astream(
            initial_state,
            config=config,
            stream_mode="custom",
            subgraphs=True,
        ):
            _, payload = event
            if not isinstance(payload, AgentEvent):
                raise TypeError(f"Expected AgentEvent, got {type(payload).__name__}")
            if isinstance(payload, AuthoredWorkflowCompleteEvent):
                terminal = payload
            elif isinstance(payload, WorkflowCompleteEvent):
                continue
            yield payload
        if terminal is None:
            raise RuntimeError("Authored delivery emitted no terminal")
        await self._cleanup_backups(terminal, config)

    def _build_delivery_graph(
        self,
    ) -> StateGraph[
        AuthoredWorkflowState, None, AuthoredWorkflowState, AuthoredWorkflowState
    ]:
        graph: StateGraph[
            AuthoredWorkflowState, None, AuthoredWorkflowState, AuthoredWorkflowState
        ] = StateGraph(AuthoredWorkflowState)
        graph.add_node("fire", self._compiled)
        graph.add_node(
            "open_pr", self._floor(self._open_pr_node), retry_policy=self._retry
        )
        graph.add_node(
            "open_stalled_pr",
            self._floor(self._open_stalled_pr_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "monitor_ci", self._floor(self._monitor_ci_node), retry_policy=self._retry
        )
        graph.add_node(
            "comment_failure",
            self._floor(self._comment_failure_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "delivery_remediation",
            self._floor(self._delivery_remediation_node),
            retry_policy=self._retry,
        )
        graph.add_node("complete", self._authored_complete_node)
        graph.add_edge(START, "fire")
        graph.add_conditional_edges(
            "fire",
            self._route_after_fire,
            {
                name: name
                for name in (
                    "open_pr",
                    "open_stalled_pr",
                    "monitor_ci",
                    "comment_failure",
                    "complete",
                )
            },
        )
        graph.add_conditional_edges(
            "open_pr",
            self._route_after_pr,
            {"monitor_ci": "monitor_ci", "complete": "complete"},
        )
        graph.add_edge("open_stalled_pr", "complete")
        graph.add_conditional_edges(
            "monitor_ci",
            self._route_after_ci,
            {
                "remediate": "delivery_remediation",
                "comment_failure": "comment_failure",
                "complete": "complete",
            },
        )
        graph.add_edge("delivery_remediation", "fire")
        graph.add_edge("comment_failure", "complete")
        graph.add_edge("complete", END)
        return graph

    def _route_after_fire(self, state: AuthoredWorkflowState) -> str:
        can_pr = self._pr_creator is not None and state["repo_url"] is not None
        if state["criteria_infeasible"] or state["merge_error"] is not None:
            return "complete"
        if not gate_cleared(state["accept_verdict"]):
            if (
                can_pr
                and state["best_iteration_sha"] is not None
                and state["trajectory"] is not None
            ):
                return "open_stalled_pr"
            return "complete"
        if not state["review_passed"]:
            if can_pr and state["pr_url"] is not None:
                return "comment_failure"
            return "complete"
        if state["pr_url"] is not None:
            if self._ci_monitor is not None and state["repo_url"] is not None:
                return "monitor_ci"
            return "complete"
        return "open_pr" if can_pr else "complete"

    async def _delivery_remediation_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        request = RemediationRequest(
            entry=RemediationEntry.ci_failure,
            round_index=state["remediation_rounds_used"],
            original_ticket=original_ticket(state),
            work_branch=state["feature_branch"],
            work_base_ref=state["work_base_ref"],
            pr_url=state["pr_url"],
            total_iterations=state["total_iterations"],
            trajectory=state["trajectory"],
            criteria=validated_criteria(state),
            failure_evidence=state["ci_summary"]
            or "CI reported a failure with no summary.",
        )
        return await self._draft_remediation(state, request, config)

    async def _open_stalled_pr_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        trajectory = state["trajectory"]
        best_sha = state["best_iteration_sha"]
        head = state["feature_branch"]
        head_sha = state["feature_tip_sha"]
        repo_url = ctx.repo_url
        if self._pr_creator is None or repo_url is None:
            raise RuntimeError("Stalled delivery requires the configured forge")
        if self._ref_publisher is None:
            raise RuntimeError("Stalled delivery requires ref_publisher")
        if trajectory is None or best_sha is None or head_sha is None:
            raise RuntimeError("Stalled delivery requires its published best head")
        ticket = current_ticket(state)
        pr_url, pr_number = await self._pr_creator.create_pr(
            repo_url=repo_url,
            title=await self._gated(
                content=stall_pr_title(ticket.title),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_TITLE,
                content_class=ContentClass.AUTHORED,
            ),
            body=require_tracker_issue(
                await self._gated(
                    content=append_tracker_issue(
                        stall_pr_body(
                            trajectory,
                            validated_criteria(state),
                            landed_commit=best_sha,
                        ),
                        state["issue_key"],
                    ),
                    visibility=state["repo_visibility"],
                    shape=WriterShape.PROSE,
                    destination=OutboundDestination.PR_BODY,
                    content_class=ContentClass.AUTHORED,
                ),
                state["issue_key"],
            ),
            head=head,
            base=ctx.base_branch,
        )
        writer(
            WorkflowPREvent(
                pr_url=pr_url,
                pr_number=pr_number,
                feature_branch=head,
                base_branch=ctx.base_branch,
                feature_tip_sha=head_sha,
                # The acceptance gate rejected this branch: the pull request
                # asks a human to read a stall, it does not deliver the issue.
                delivered=False,
            )
        )
        return {"pr_url": pr_url, "pr_number": pr_number}

    async def _open_pr_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Open a pull request for the feature branch."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        pr_creator = self._pr_creator
        if pr_creator is None:
            msg = "open_pr requires pr_creator but self._pr_creator is None"
            raise RuntimeError(msg)

        repo_url = ctx.repo_url
        if repo_url is None:
            msg = "open_pr requires repo_url but ctx.repo_url is None"
            raise RuntimeError(msg)

        ticket = current_ticket(state)

        feature_tip_sha = state["feature_tip_sha"]
        if feature_tip_sha is None:
            msg = "open_pr requires feature_tip_sha to be set."
            raise RuntimeError(msg)

        if self._artifact_persister is not None:
            await self._artifact_persister.clean(
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                branch=state["feature_branch"],
                cache_key=ctx.cache_key,
            )

        # Generate PR description via agent
        prompt = self._prompts.template_for(PromptKey.PR_DESCRIPTION).render(
            {
                "task_md": format_fire_spec(AuthoredSpec(ticket=ticket)),
                "acceptance_criteria": validated_criteria(state),
                "total_iterations": state["total_iterations"],
            },
        )
        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=[],
                skills=self._prompts.session_skills(
                    PromptKey.PR_DESCRIPTION, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                run_identity=ctx.run_identity,
                session_policy=self._prompts.session_policy(
                    PromptKey.PR_DESCRIPTION,
                ),
                output_format={
                    "type": "json_schema",
                    "schema": PR_DESCRIPTION_SCHEMA,
                },
                cache_key=ctx.cache_key,
            ),
            site="pr_description",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for PR description"
            raise soft_failure(
                msg,
                raise_site="pr_description",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        pr_output = PRDescriptionOutput.model_validate(
            result_event.structured_output,
        )
        body = append_tracker_issue(
            append_flagged_section(pr_output.description, state["flagged_items"]),
            state["issue_key"],
        )

        pr_url, pr_number = await pr_creator.create_pr(
            repo_url=repo_url,
            title=await self._gated(
                content=pr_output.title,
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_TITLE,
                content_class=ContentClass.AUTHORED,
            ),
            body=require_tracker_issue(
                await self._gated(
                    content=body,
                    visibility=state["repo_visibility"],
                    shape=WriterShape.PROSE,
                    destination=OutboundDestination.PR_BODY,
                    content_class=ContentClass.AUTHORED,
                ),
                state["issue_key"],
            ),
            head=state["feature_branch"],
            base=ctx.base_branch,
        )

        writer(
            WorkflowPREvent(
                pr_url=pr_url,
                pr_number=pr_number,
                feature_branch=state["feature_branch"],
                base_branch=ctx.base_branch,
                feature_tip_sha=feature_tip_sha,
                # The accepted path: this branch is what the run delivered.
                delivered=True,
            )
        )

        return {"pr_url": pr_url, "pr_number": pr_number}

    async def _monitor_ci_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Poll CI status for the latest commit on the feature branch."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ci_monitor = self._ci_monitor
        if ci_monitor is None:
            msg = "monitor_ci requires ci_monitor but self._ci_monitor is None"
            raise RuntimeError(msg)

        repo_url = ctx.repo_url
        if repo_url is None:
            msg = "monitor_ci requires repo_url but ctx.repo_url is None"
            raise RuntimeError(msg)

        ref = state["feature_branch"]
        passed, summary = await ci_monitor.wait_for_checks(
            repo_url=repo_url,
            ref=ref,
        )
        ci_status = ci_status_of(passed)

        writer(
            WorkflowCIEvent(
                ci_status=ci_status,
                summary=summary,
                ref=ref,
            )
        )

        return {"ci_status": ci_status, "ci_summary": summary}

    def _route_after_ci(self, state: AuthoredWorkflowState) -> str:
        """Route based on CI result, fix budget, and adapter preconditions."""
        if state["ci_status"] is not CIStatus.failed:
            return "complete"
        if self._rounds_remain(state):
            return "remediate"
        can_comment = (
            state["pr_number"] is not None
            and self._pr_creator is not None
            and state.get("repo_url") is not None
        )
        if can_comment:
            return "comment_failure"
        return "complete"

    async def _comment_failure_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Post a comment on the PR about exhausted fix budget.

        A forge refusal on this last write is LOGGED and the run continues
        to its terminal event: the comment reports a failure the terminal
        event also reports, so crashing here would lose the whole outcome
        in order to report that one line of it did not post.  The
        containment is exactly the forge taxonomy — ``ForgeAPIError`` and
        ``TransientAPIError`` — and every other exception propagates,
        because a defect in this node is not a forge refusal and must not
        be filed as one.
        """
        ctx = ExecutionContext.from_configurable(config)

        pr_creator = self._pr_creator
        if pr_creator is None:
            msg = "comment_failure requires pr_creator but self._pr_creator is None"
            raise RuntimeError(msg)

        repo_url = ctx.repo_url
        if repo_url is None:
            msg = "comment_failure requires repo_url but ctx.repo_url is None"
            raise RuntimeError(msg)

        pr_number = state["pr_number"]
        if pr_number is None:
            msg = "comment_failure requires pr_number but state['pr_number'] is None"
            raise RuntimeError(msg)

        comment_parts = [
            "## kodezart: remediation budget exhausted\n",
            (
                f"Remediation rounds used: {state['remediation_rounds_used']}"
                f"/{self._remediation_max_rounds}\n"
            ),
        ]
        if state["review_feedback"] is not None:
            comment_parts.append(f"\n### Review Failures\n{state['review_feedback']}\n")
        if state["ci_summary"] is not None:
            comment_parts.append(f"\n### CI Summary\n{state['ci_summary']}\n")

        # AUTHORED: the counters above are derived, but review_feedback is
        # the evaluator's own reasoning per failed criterion. One authored
        # part makes the assembled body authored.
        comment_body = await self._gated(
            content="".join(comment_parts),
            visibility=state["repo_visibility"],
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_COMMENT,
            content_class=ContentClass.AUTHORED,
        )

        try:
            await pr_creator.comment_on_pr(
                repo_url=repo_url,
                pr_number=pr_number,
                body=comment_body,
            )
        except (ForgeAPIError, TransientAPIError) as exc:
            await self._log.aerror(
                "comment_failure_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
            )

        return {}

    def _route_after_pr(self, state: AuthoredWorkflowState) -> str:
        """Route after PR creation: monitor CI only if adapter is configured."""
        if self._ci_monitor is not None and state.get("repo_url") is not None:
            return "monitor_ci"
        return "complete"

    async def _authored_complete_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Emit the authored terminal after delivery."""
        _ = config
        writer = get_stream_writer()
        writer(
            AuthoredWorkflowCompleteEvent(
                feature_branch=state["feature_branch"],
                ralph_branch=state["ralph_branch"],
                total_iterations=state["total_iterations"],
                accepted=gate_cleared(state["accept_verdict"]),
                outcome=classify_authored_outcome(state),
                merged=state["merged"],
                final_commit_sha=state["feature_tip_sha"],
                merge_error=state["merge_error"],
                pr_url=state["pr_url"],
                pr_number=state["pr_number"],
                ci_status=state["ci_status"],
                trajectory=state["trajectory"],
                criteria_validation=state["criteria_validation"],
            )
        )

        return {}
