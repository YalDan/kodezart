"""Ralph workflow engine — outer pipeline: branch generation, loop, post-merge."""

import uuid
from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
    EVAL_TOOLS,
    EVAL_TOOLS_WITH_AGENT,
)
from kodezart.core.errors import NoStructuredOutputError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    BranchMerger,
    CIMonitor,
    GitService,
    OutboundContentGate,
    PRCreator,
    PromptProvider,
    QualityGate,
    RepoCache,
    RepoVisibilityResolver,
    TicketGenerator,
)
from kodezart.core.retry import should_retry
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import (
    append_flagged_section,
    flagged_items,
    gate_cleared,
)
from kodezart.domain.agent import generate_ralph_branch_name
from kodezart.domain.base_scope import scope_base
from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import (
    demands_regeneration,
    regeneration_targets,
    sweep,
)
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.outcome import classify_outcome
from kodezart.domain.prompt_variables import (
    changeset_variables,
    render_validation_findings,
)
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    BRANCH_NAME_SCHEMA,
    CRITERIA_VALIDATION_SCHEMA,
    GENERATED_CRITERIA_SCHEMA,
    PR_DESCRIPTION_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    BranchNameOutput,
    GeneratedCriteriaOutput,
    PRDescriptionOutput,
    TicketDraftOutput,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowConsolidationEvent,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
    WorkflowReviewEvent,
    WorkflowScopeBaseEvent,
    WorkflowTicketEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.base_spec import BaseRefRole, BaseSpec
from kodezart.types.domain.consolidation import ConsolidationStatus
from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriteriaValidationOutput,
    ValidatedCriterion,
)
from kodezart.types.domain.gating import (
    GateVerdict,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState


def _validated_artifact(state: WorkflowState) -> CriteriaArtifact:
    """The post-sweep criteria document. Raises if the sweep has not run.

    Every surface downstream of ``validate_criteria`` — the loop, the
    review, the fix round and the PR body — reads criteria through this
    one accessor, so no consumer can pick up the pre-sweep list and
    dispatch a criterion without the verdict the sweep computed for it.
    """
    artifact = state["criteria_artifact"]
    if artifact is None:
        msg = "Criteria are read after the sweep; state['criteria_artifact'] is None."
        raise RuntimeError(msg)
    return artifact


def _validated_criteria(state: WorkflowState) -> list[ValidatedCriterion]:
    """The criteria as validated — identity, text, classification, verdict."""
    return _validated_artifact(state).criteria


class RalphWorkflowEngine:
    """Outer workflow: branch -> ticket -> criteria -> ralph loop -> post-merge.

    Delegates the iterative execute/evaluate loop to a QualityGate.
    Post-merge: review against ticket, open PR, monitor CI, fix failures.
    """

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
        prompts: PromptProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        visibility_resolver: RepoVisibilityResolver | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int = 3,
        retry_initial_interval: float = 1.0,
        pr_creator: PRCreator | None = None,
        ci_monitor: CIMonitor | None = None,
        max_fix_rounds: int = 2,
        criteria_max_regeneration_rounds: int = 1,
        artifact_persister: ArtifactPersister | None = None,
    ) -> None:
        self._service: AgentRunner = service
        self._quality_gate: QualityGate = quality_gate
        self._ticket_generator: TicketGenerator = ticket_generator
        self._merger: BranchMerger = merger
        self._git_base_url: str = git_base_url
        self._git_remote: str = git_remote
        # git/cache are injected so _review_against_ticket_node and the
        # consolidation nodes can pre-compute ChangesetDigest /
        # query canonical SHAs without leaking shell into the workflow body.
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._prompts: PromptProvider = prompts
        self._skills: SkillsSelection = skills
        self._gate: OutboundContentGate = gate
        self._visibility_resolver: RepoVisibilityResolver | None = visibility_resolver
        self._pr_creator: PRCreator | None = pr_creator
        self._ci_monitor: CIMonitor | None = ci_monitor
        self._max_fix_rounds: int = max_fix_rounds
        self._criteria_max_regeneration_rounds: int = criteria_max_regeneration_rounds
        self._artifact_persister: ArtifactPersister | None = artifact_persister
        self._retry: RetryPolicy = RetryPolicy(
            max_attempts=retry_max_attempts,
            initial_interval=retry_initial_interval,
            retry_on=should_retry,
        )
        self._log: BoundLogger = get_logger(__name__)
        self._checkpointer: BaseCheckpointSaver[str] | None = checkpointer
        self._compiled = self._build_graph().compile(
            checkpointer=self._checkpointer,
        )

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        implied_base: BaseSpec | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Execute the full workflow pipeline.

        *base_spec* is the lane's recorded base and *implied_base* is the
        base its blockers imply now; the run refuses before any node when
        they differ, because a criterion graded against a base that has
        moved is about a tree that no longer exists.

        ``cache_key`` IS the LangGraph thread id: the caller's job id
        addresses this run's checkpoints.
        """
        # TODO(time-travel): E2E checkpoint resume still requires:
        # 2. On resume: pass None (not initial_state) to astream()
        #    so LangGraph loads from the outer checkpoint.
        # 4. Sub-graphs are called imperatively (not LangGraph
        #    subgraphs), so each has isolated checkpoints. On outer
        #    resume the sub-graph nodes re-enter; inner loops must
        #    also accept a resume signal (see ralph_loop.py and
        #    ticket_generation.py TODOs).
        # 5. WorkflowRequest and the handler need a resume signal to
        #    plumb an existing job id back in from HTTP.
        # Refuses here, before any node: a stale baseline produces no
        # scope verdict at all rather than one graded against the wrong tree.
        scope_base(base_spec, implied_base)

        resolved_url = (
            resolve_repo_url(repo_url, self._git_base_url)
            if repo_url is not None
            else None
        )

        ctx = ExecutionContext(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=resolved_url,
            cache_key=cache_key,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = workflow_thread_id(cache_key)

        config: RunnableConfig = {"configurable": configurable}

        initial_state: WorkflowState = {
            "feature_branch": "",
            "ralph_branch": "",
            "ticket": None,
            "acceptance_criteria": [],
            "criteria_artifact": None,
            "criteria_validation": None,
            "criteria_regeneration_rounds": 0,
            "criteria_infeasible": False,
            "accept_verdict": AcceptVerdict.rejected,
            "flagged_items": [],
            "total_iterations": 0,
            "feature_tip_sha": None,
            "review_base_sha": None,
            "review_head_sha": None,
            "merged": False,
            "merge_error": None,
            "review_passed": False,
            "review_feedback": None,
            "fix_rounds_used": 0,
            "pr_url": None,
            "pr_number": None,
            "ci_passed": None,
            "ci_summary": None,
            "repo_url": resolved_url,
            "repo_visibility": RepoVisibility.UNKNOWN,
            "trajectory": None,
        }

        async for event in self._compiled.astream(
            initial_state,
            config=config,
            stream_mode="custom",
        ):
            if not isinstance(event, AgentEvent):
                msg = f"Expected AgentEvent from stream, got {type(event).__name__}"
                raise TypeError(msg)
            yield event

    # -- Graph construction --------------------------------------------------

    def _build_graph(
        self,
    ) -> StateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
        graph: StateGraph[WorkflowState, None, WorkflowState, WorkflowState] = (
            StateGraph(WorkflowState)
        )
        graph.add_node(
            "resolve_visibility",
            self._resolve_visibility_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_branch",
            self._generate_branch_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_ticket",
            self._generate_ticket_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_criteria",
            self._generate_criteria_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "validate_criteria",
            self._validate_criteria_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "run_ralph_loop",
            self._run_ralph_loop_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "merge_to_feature",
            self._merge_to_feature_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "review_against_ticket",
            self._review_against_ticket_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "fix_code",
            self._fix_code_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "open_pr",
            self._open_pr_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "monitor_ci",
            self._monitor_ci_node,
            retry_policy=self._retry,
        )
        graph.add_node(
            "comment_failure",
            self._comment_failure_node,
            retry_policy=self._retry,
        )
        graph.add_node("complete", self._complete_node)

        if self._artifact_persister is not None:
            graph.add_node(
                "persist_artifacts",
                self._persist_artifacts_node,
                retry_policy=self._retry,
            )

        graph.add_edge(START, "resolve_visibility")
        graph.add_edge("resolve_visibility", "generate_branch")
        graph.add_edge("generate_branch", "generate_ticket")
        graph.add_edge("generate_ticket", "generate_criteria")
        graph.add_edge("generate_criteria", "validate_criteria")
        proceed = (
            "persist_artifacts"
            if self._artifact_persister is not None
            else "run_ralph_loop"
        )
        graph.add_conditional_edges(
            "validate_criteria",
            self._route_after_criteria_validation,
            {
                "generate_criteria": "generate_criteria",
                proceed: proceed,
                "complete": "complete",
            },
        )
        if self._artifact_persister is not None:
            graph.add_edge("persist_artifacts", "run_ralph_loop")
        graph.add_edge("run_ralph_loop", "merge_to_feature")
        graph.add_conditional_edges(
            "merge_to_feature",
            self._route_after_merge,
            {"review_against_ticket": "review_against_ticket", "complete": "complete"},
        )
        graph.add_conditional_edges(
            "review_against_ticket",
            self._route_after_review,
            {
                "open_pr": "open_pr",
                "monitor_ci": "monitor_ci",
                "fix_code": "fix_code",
                "complete": "complete",
                "comment_failure": "comment_failure",
            },
        )
        graph.add_conditional_edges(
            "fix_code",
            self._route_after_fix,
            {
                "review_against_ticket": "review_against_ticket",
                "comment_failure": "comment_failure",
                "complete": "complete",
            },
        )
        graph.add_conditional_edges(
            "open_pr",
            self._route_after_pr,
            {"monitor_ci": "monitor_ci", "complete": "complete"},
        )
        graph.add_conditional_edges(
            "monitor_ci",
            self._route_after_ci,
            {
                "complete": "complete",
                "fix_code": "fix_code",
                "comment_failure": "comment_failure",
            },
        )
        graph.add_edge("comment_failure", "complete")
        graph.add_edge("complete", END)
        return graph

    # -- Existing nodes ------------------------------------------------------

    async def _resolve_visibility_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Resolve repository visibility ONCE, before any gated writer runs.

        Fail-closed with no exemption: a resolution failure, a deployment
        with no forge client, and a local-only run all yield UNKNOWN, which
        takes the public path with the gate engaged.
        """
        _ = state  # required by LangGraph but unused in this node
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        visibility = RepoVisibility.UNKNOWN
        if self._visibility_resolver is not None and ctx.repo_url is not None:
            visibility = await self._visibility_resolver.resolve_visibility(
                repo_url=ctx.repo_url,
            )

        await self._log.ainfo(
            "repo_visibility_resolved",
            visibility=visibility.value,
            repo_url=ctx.repo_url,
        )
        writer(
            WorkflowVisibilityEvent(
                visibility=visibility,
                repo_url=ctx.repo_url,
            )
        )
        # Stated once, before any surface compares anything against it.
        writer(
            WorkflowScopeBaseEvent(
                base_ref=ctx.base_spec.base_ref,
                role=ctx.base_spec.role,
                inputs=list(ctx.base_spec.inputs),
            )
        )
        await self._log.ainfo(
            "scope_base_resolved",
            base_ref=ctx.base_spec.base_ref,
            role=ctx.base_spec.role.value,
            input_count=len(ctx.base_spec.inputs),
        )
        return {"repo_visibility": visibility}

    async def _gated(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        writer_name: str,
    ) -> str:
        """Route one outbound payload through the gate. BLOCKED raises."""
        decision = self._gate.gate(
            content=content,
            visibility=visibility,
            shape=shape,
        )
        await self._log.ainfo(
            "outbound_content_gated",
            writer=writer_name,
            verdict=decision.verdict.value,
            visibility=visibility.value,
            categories=[c.value for c in decision.categories],
        )
        if decision.verdict is GateVerdict.BLOCKED:
            msg = "Outbound content blocked before write"
            raise OutboundContentBlockedError(
                msg,
                writer=writer_name,
                categories=[c.value for c in decision.categories],
            )
        return decision.content

    async def _generate_branch_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Ask the agent to generate a descriptive branch name."""
        ctx = ExecutionContext.from_configurable(config)
        branch_prompt = self._prompts.template_for(PromptKey.BRANCH_NAME).render(
            {"task": ctx.prompt},
        )
        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=branch_prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=[],
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": BRANCH_NAME_SCHEMA,
                },
                cache_key=ctx.cache_key,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for branch name"
            raise NoStructuredOutputError(
                msg,
                raise_site="branch_name",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        output = BranchNameOutput.model_validate(result_event.structured_output)
        slug = await self._gated(
            content=output.slug,
            visibility=state["repo_visibility"],
            shape=WriterShape.IDENTIFIER,
            writer_name="branch_name",
        )
        feature_branch = f"kodezart/{slug}-{uuid.uuid4().hex[:8]}"
        ralph_branch = generate_ralph_branch_name(feature_branch)
        return {"feature_branch": feature_branch, "ralph_branch": ralph_branch}

    async def _generate_ticket_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Generate a structured ticket from the raw user prompt."""
        _ = state  # required by LangGraph but unused in this node
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket_event: WorkflowTicketEvent | None = None
        async for event in self._ticket_generator.run(
            prompt=ctx.prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            cache_key=ctx.cache_key,
            base_branch=ctx.base_branch,
        ):
            writer(event)
            if isinstance(event, WorkflowTicketEvent):
                ticket_event = event

        if ticket_event is None:
            msg = "Ticket generator did not emit a WorkflowTicketEvent."
            raise RuntimeError(msg)

        return {"ticket": ticket_event.ticket}

    async def _generate_criteria_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Ask the agent to analyze the codebase and generate acceptance criteria."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket = state["ticket"]
        if ticket is None:
            msg = "generate_criteria requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        prompt = self._prompts.template_for(PromptKey.ACCEPTANCE_CRITERIA).render(
            {
                "task_description": format_ticket_as_task(ticket),
                "validation_findings": render_validation_findings(
                    state["acceptance_criteria"],
                    state["criteria_validation"],
                ),
                "base_ref": ctx.base_branch,
            },
        )

        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                branch=ctx.base_branch,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=EVAL_TOOLS_WITH_AGENT,
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": GENERATED_CRITERIA_SCHEMA,
                },
                cache_key=ctx.cache_key,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for acceptance criteria"
            raise NoStructuredOutputError(
                msg,
                raise_site="acceptance_criteria",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        output = GeneratedCriteriaOutput.model_validate(
            result_event.structured_output,
        )
        criteria = list(mint_criteria(output.criteria))

        writer(
            WorkflowCriteriaEvent(
                criteria=criteria,
                reasoning=output.reasoning,
            )
        )

        return {"acceptance_criteria": criteria}

    async def _validate_criteria_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Sweep the generated criteria for feasibility against the base ref.

        The refuter reports evidence; :func:`sweep` computes the verdicts.
        A set that still demands regeneration once the bound is spent halts
        the run here — before the loop, with the sweep as its report.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket = state["ticket"]
        if ticket is None:
            msg = "validate_criteria requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        criteria = state["acceptance_criteria"]
        prompt = self._prompts.template_for(PromptKey.CRITERIA_VALIDATION).render(
            {
                "task_description": format_ticket_as_task(ticket),
                "acceptance_criteria": criteria,
                "base_ref": ctx.base_branch,
            },
        )

        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                branch=ctx.base_branch,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=EVAL_TOOLS,
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": CRITERIA_VALIDATION_SCHEMA,
                },
                cache_key=ctx.cache_key,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for criteria validation"
            raise NoStructuredOutputError(
                msg,
                raise_site="criteria_validation",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        validation = sweep(
            criteria,
            CriteriaValidationOutput.model_validate(result_event.structured_output),
        )
        targets = regeneration_targets(validation)
        rounds_used = state["criteria_regeneration_rounds"]
        bound_exhausted = (
            bool(targets) and rounds_used >= self._criteria_max_regeneration_rounds
        )

        writer(
            WorkflowCriteriaValidationEvent(
                regeneration_round=rounds_used,
                validation=validation,
                regeneration_targets=list(targets),
            )
        )
        await self._log.ainfo(
            "criteria_sweep_complete",
            regeneration_round=rounds_used,
            regeneration_targets=list(targets),
            satisfiable=validation.conjunction.satisfiable,
            bound_exhausted=bound_exhausted,
        )

        return {
            "criteria_validation": validation,
            "criteria_artifact": build_artifact(criteria, validation),
            "criteria_regeneration_rounds": (
                rounds_used if bound_exhausted or not targets else rounds_used + 1
            ),
            "criteria_infeasible": bound_exhausted,
        }

    def _route_after_criteria_validation(self, state: WorkflowState) -> str:
        """Halt, regenerate, or proceed — computed from the sweep alone."""
        if state["criteria_infeasible"]:
            return "complete"
        validation = state["criteria_validation"]
        if validation is not None and demands_regeneration(validation):
            return "generate_criteria"
        return (
            "persist_artifacts"
            if self._artifact_persister is not None
            else "run_ralph_loop"
        )

    async def _run_quality_gate(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[ValidatedCriterion],
        cache_key: str,
        repo_visibility: RepoVisibility,
    ) -> WorkflowIterationEvent:
        """Delegate to the quality gate for iterative execution."""
        writer = get_stream_writer()
        last_iteration_event: WorkflowIterationEvent | None = None
        async for event in self._quality_gate.run(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            acceptance_criteria=acceptance_criteria,
            cache_key=cache_key,
            repo_visibility=repo_visibility,
        ):
            writer(event)
            if isinstance(event, WorkflowIterationEvent):
                last_iteration_event = event

        if last_iteration_event is None:
            msg = "Ralph loop completed without emitting an iteration event."
            raise RuntimeError(msg)

        return last_iteration_event

    async def _run_ralph_loop_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Delegate to the quality gate for iterative execution."""
        ctx = ExecutionContext.from_configurable(config)

        ticket = state["ticket"]
        if ticket is None:
            msg = "run_ralph_loop requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        implementation_prompt = self._prompts.template_for(
            PromptKey.IMPLEMENTATION,
        ).render({"task_md": format_ticket_as_task(ticket)})

        last_iteration_event = await self._run_quality_gate(
            prompt=implementation_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            feature_branch=state["feature_branch"],
            ralph_branch=state["ralph_branch"],
            base_spec=ctx.base_spec,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            acceptance_criteria=_validated_criteria(state),
            cache_key=ctx.cache_key,
            repo_visibility=state["repo_visibility"],
        )

        # feature_tip_sha is left None here; _merge_to_feature_node sets it
        # from the merger's outcome.feature_tip_sha (canonical, post-push).
        return {
            "accept_verdict": last_iteration_event.verdict,
            "flagged_items": flagged_items(
                _validated_criteria(state),
                last_iteration_event.evaluation.criteria_results,
                last_iteration_event.evaluation.sherlock_flags,
            ),
            "total_iterations": last_iteration_event.iteration,
            "feature_tip_sha": None,
            "trajectory": last_iteration_event.trajectory,
        }

    async def _persist_artifacts_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Persist ticket and criteria to .kodezart/ on the ralph branch."""
        ctx = ExecutionContext.from_configurable(config)

        if self._artifact_persister is None:
            msg = "persist_artifacts node requires artifact_persister"
            raise RuntimeError(msg)

        ticket: TicketDraftOutput | None = state["ticket"]
        if ticket is None:
            msg = "persist_artifacts requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        criteria_artifact = _validated_artifact(state)

        artifacts: dict[str, str] = {
            "ticket.json": await self._gated(
                content=ticket.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                writer_name="artifact_ticket_json",
            ),
            "criteria.json": await self._gated(
                content=criteria_artifact.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                writer_name="artifact_criteria_json",
            ),
        }

        await self._artifact_persister.persist(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["ralph_branch"],
            base_branch=ctx.base_branch,
            artifacts=artifacts,
            cache_key=ctx.cache_key,
        )
        # TODO(artifact-resume): On checkpoint resume, check if artifacts
        # already exist on the branch and skip regeneration. Requires the
        # HTTP→handler→engine thread_id plumbing in ralph_workflow.py:130-145.
        return {}

    # -- Post-merge nodes ----------------------------------------------------

    async def _merge_to_feature_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Consolidate ralph branch into feature branch.

        Single ``BranchMerger.consolidate`` call; routes on the four-status
        outcome.  Never catches exceptions around the merger — the merger
        is a total function over the four statuses.  ``SOURCE_MISSING`` is
        a programming error (the loop must have pushed) and raises.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if not gate_cleared(state["accept_verdict"]):
            return {
                "merged": False,
                "merge_error": None,
                "feature_tip_sha": None,
                "review_base_sha": None,
                "review_head_sha": None,
            }

        outcome = await self._merger.consolidate(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=ctx.base_branch,
            feature_branch=state["feature_branch"],
            source_branch=state["ralph_branch"],
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowConsolidationEvent(
                status=outcome.status,
                feature_branch=state["feature_branch"],
                source_branch=state["ralph_branch"],
                feature_tip_sha=outcome.feature_tip_sha,
            )
        )

        if outcome.status is ConsolidationStatus.SOURCE_MISSING:
            msg = (
                f"consolidate returned SOURCE_MISSING for "
                f"{state['ralph_branch']!r} — the loop must have pushed"
            )
            raise RuntimeError(msg)

        if outcome.status is ConsolidationStatus.DIVERGENT:
            return {
                "merged": False,
                "merge_error": (
                    f"ralph diverged from feature: "
                    f"{state['ralph_branch']} ⇄ {state['feature_branch']}"
                ),
                "feature_tip_sha": outcome.feature_tip_sha,
                "review_base_sha": None,
                "review_head_sha": None,
            }

        # FAST_FORWARDED or ALREADY_INTEGRATED — both yield a merged
        # feature tip the reviewer can evaluate against the base branch.
        cwd = await self._resolve_cwd(ctx)
        base_tip = await self._git.remote_branch_sha(
            cwd,
            self._git_remote,
            ctx.base_branch,
        )
        if base_tip is None:
            msg = (
                f"Base branch {ctx.base_branch!r} not found on {self._git_remote} "
                "after successful consolidation"
            )
            raise RuntimeError(msg)
        return {
            "merged": True,
            "merge_error": None,
            "feature_tip_sha": outcome.feature_tip_sha,
            "review_base_sha": base_tip,
            "review_head_sha": outcome.feature_tip_sha,
        }

    def _route_after_merge(self, state: WorkflowState) -> str:
        """Guard: only review merged code — skip to complete if not merged."""
        if state["merged"]:
            return "review_against_ticket"
        return "complete"

    async def _resolve_cwd(self, ctx: ExecutionContext) -> str:
        """Resolve a usable cwd for GitService calls in the outer engine."""
        if ctx.repo_path is not None:
            return ctx.repo_path
        if ctx.repo_url is None:
            msg = "Neither repo_path nor repo_url set on ExecutionContext"
            raise RuntimeError(msg)
        return await self._cache.ensure_available(ctx.repo_url, ctx.cache_key)

    async def _review_against_ticket_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Evaluate merged code against ticket acceptance criteria."""
        # Fail-fast on programming-error preconditions BEFORE touching the
        # LangGraph runtime (so callers — and tests — see the precondition
        # error, not a misleading "outside of a runnable context" error).
        review_base_sha = state["review_base_sha"]
        review_head_sha = state["review_head_sha"]
        if review_base_sha is None or review_head_sha is None:
            msg = (
                "review_against_ticket requires review_base_sha and "
                "review_head_sha to be set by the consolidation node"
            )
            raise RuntimeError(msg)

        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        cwd = await self._resolve_cwd(ctx)
        changeset = await self._git.diff_summary(
            cwd=cwd,
            base_ref=review_base_sha,
            head_ref=review_head_sha,
        )
        prompt = self._prompts.template_for(PromptKey.POST_MERGE_REVIEW).render(
            {
                "criteria": _validated_criteria(state),
                **changeset_variables(changeset),
            },
        )

        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                branch=state["feature_branch"],
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=EVAL_TOOLS,
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                },
                cache_key=ctx.cache_key,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for review"
            raise NoStructuredOutputError(
                msg,
                raise_site="post_merge_review",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        grade = grade_iteration(
            _validated_criteria(state),
            AcceptanceCriteriaOutput.model_validate(result_event.structured_output),
        )
        if grade.missing_ids or grade.unknown_ids or grade.duplicate_ids:
            await self._log.awarning(
                "review_result_reconciliation",
                dispatched_count=grade.dispatched_count,
                missing_ids=grade.missing_ids,
                unknown_ids=grade.unknown_ids,
                duplicate_ids=grade.duplicate_ids,
            )
        passed = gate_cleared(grade.verdict)

        feedback: str | None = None
        if not passed:
            feedback = "\n".join(
                f"- {f.criterion_id} {f.text}: {f.reasoning}" for f in grade.failures
            )

        writer(
            WorkflowReviewEvent(
                passed=passed,
                evaluation=AcceptanceCriteriaOutput(criteria_results=grade.results),
                fix_round=state["fix_rounds_used"],
            )
        )

        return {"review_passed": passed, "review_feedback": feedback}

    def _route_after_review(self, state: WorkflowState) -> str:
        """Route based on review result, fix budget, and adapter preconditions."""
        can_pr = self._pr_creator is not None and state.get("repo_url") is not None
        can_ci = self._ci_monitor is not None and state.get("repo_url") is not None
        if state["review_passed"]:
            if state["pr_url"] is not None and can_ci:
                return "monitor_ci"
            if state["pr_url"] is not None:
                return "complete"
            if can_pr:
                return "open_pr"
            return "complete"
        if state["fix_rounds_used"] < self._max_fix_rounds:
            return "fix_code"
        if state["pr_url"] is not None and can_pr:
            return "comment_failure"
        return "complete"

    def _route_after_pr(self, state: WorkflowState) -> str:
        """Route after PR creation: monitor CI only if adapter is configured."""
        if self._ci_monitor is not None and state.get("repo_url") is not None:
            return "monitor_ci"
        return "complete"

    async def _fix_code_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Create a fix branch, run the agent, consolidate into feature.

        Key correctness fix: the consolidation's ``base_branch`` is the
        feature branch (not ``ctx.base_branch``) so the worktree is
        acquired atop the feature tip — closing the bug where the fix
        attempted to merge atop ``main``.  No ``try/except`` around the
        merger.  Routing decisions live in ``_route_after_fix``.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        fix_branch = generate_ralph_branch_name(state["feature_branch"])

        ticket = state["ticket"]
        if ticket is None:
            msg = "fix_code requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        pre_fix_head_sha = state["feature_tip_sha"]
        if pre_fix_head_sha is None:
            msg = "fix_code requires feature_tip_sha to be set."
            raise RuntimeError(msg)

        fix_prompt = self._prompts.template_for(PromptKey.FIX).render(
            {
                "task_md": format_ticket_as_task(ticket),
                "review_feedback": state["review_feedback"],
                "ci_summary": state["ci_summary"],
            },
        )

        gate_event = await self._run_quality_gate(
            prompt=fix_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            feature_branch=state["feature_branch"],
            ralph_branch=fix_branch,
            # The fix round measures itself against this lane's own
            # deliverable ref, which is what the feature branch IS.
            base_spec=BaseSpec(
                base_ref=state["feature_branch"],
                role=BaseRefRole.deliverable,
            ),
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            acceptance_criteria=_validated_criteria(state),
            cache_key=ctx.cache_key,
            repo_visibility=state["repo_visibility"],
        )

        outcome = await self._merger.consolidate(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=state["feature_branch"],
            feature_branch=state["feature_branch"],
            source_branch=fix_branch,
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowConsolidationEvent(
                status=outcome.status,
                feature_branch=state["feature_branch"],
                source_branch=fix_branch,
                feature_tip_sha=outcome.feature_tip_sha,
            )
        )

        # Terminal totals are cumulative across rounds: total_iterations is a
        # SUM, accepted is last-round-wins, trajectory is the most recent gate
        # invocation's.
        cumulative: dict[str, object] = {
            "fix_rounds_used": state["fix_rounds_used"] + 1,
            "accept_verdict": gate_event.verdict,
            "flagged_items": flagged_items(
                _validated_criteria(state),
                gate_event.evaluation.criteria_results,
                gate_event.evaluation.sherlock_flags,
            ),
            "total_iterations": state["total_iterations"] + gate_event.iteration,
            "trajectory": gate_event.trajectory,
        }

        if outcome.status in (
            ConsolidationStatus.DIVERGENT,
            ConsolidationStatus.SOURCE_MISSING,
        ):
            return {
                **cumulative,
                "feature_tip_sha": pre_fix_head_sha,
                "merged": False,
                "merge_error": (
                    f"fix consolidation failed: status={outcome.status.value}"
                ),
                "review_base_sha": None,
                "review_head_sha": None,
                "ci_passed": False,
                "ci_summary": None,
            }

        # FAST_FORWARDED or ALREADY_INTEGRATED — both yield a fresh
        # tip the reviewer can evaluate against the pre-fix tip.
        return {
            **cumulative,
            "feature_tip_sha": outcome.feature_tip_sha,
            "merged": True,
            "merge_error": None,
            "review_base_sha": pre_fix_head_sha,
            "review_head_sha": outcome.feature_tip_sha,
            "ci_summary": None,
        }

    def _route_after_fix(self, state: WorkflowState) -> str:
        """Route after fix_code: review on success, comment_failure on diverge.

        Empty-changeset (ALREADY_INTEGRATED) reviews are allowed to
        proceed — the prompt's escape clause fires and the reviewer
        returns ``passed=False``, advancing fix_rounds_used.  This is
        intentional truth-telling.
        """
        if state["merge_error"] is None:
            return "review_against_ticket"
        if state["pr_url"] is not None and self._pr_creator is not None:
            return "comment_failure"
        return "complete"

    async def _open_pr_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Open a pull request for the feature branch."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if self._pr_creator is None:
            await self._log.awarning("pr_creator_not_configured")
            return {"pr_url": None, "pr_number": None}

        repo_url = ctx.repo_url
        if repo_url is None:
            msg = "open_pr requires repo_url but ctx.repo_url is None"
            raise RuntimeError(msg)

        pr_creator = self._pr_creator
        if pr_creator is None:
            msg = "open_pr requires pr_creator but self._pr_creator is None"
            raise RuntimeError(msg)

        ticket = state["ticket"]
        if ticket is None:
            msg = "open_pr requires a ticket but state['ticket'] is None."
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
                "task_md": format_ticket_as_task(ticket),
                "acceptance_criteria": _validated_criteria(state),
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
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": PR_DESCRIPTION_SCHEMA,
                },
                cache_key=ctx.cache_key,
            )
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for PR description"
            raise NoStructuredOutputError(
                msg,
                raise_site="pr_description",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        pr_output = PRDescriptionOutput.model_validate(
            result_event.structured_output,
        )

        pr_url, pr_number = await pr_creator.create_pr(
            repo_url=repo_url,
            title=await self._gated(
                content=pr_output.title,
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                writer_name="pr_title",
            ),
            body=await self._gated(
                content=append_flagged_section(
                    pr_output.description,
                    state["flagged_items"],
                ),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                writer_name="pr_body",
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
            )
        )

        return {"pr_url": pr_url, "pr_number": pr_number}

    async def _monitor_ci_node(
        self,
        state: WorkflowState,
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

        writer(
            WorkflowCIEvent(
                passed=passed,
                summary=summary,
                ref=ref,
            )
        )

        return {"ci_passed": passed, "ci_summary": summary}

    def _route_after_ci(self, state: WorkflowState) -> str:
        """Route based on CI result, fix budget, and adapter preconditions."""
        if state["ci_passed"] is True:
            return "complete"
        if state["ci_passed"] is None:
            return "complete"
        if state["fix_rounds_used"] < self._max_fix_rounds:
            return "fix_code"
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
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Post a comment on the PR about exhausted fix budget."""
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
            "## kodezart: automated fix budget exhausted\n",
            f"Fix rounds used: {state['fix_rounds_used']}/{self._max_fix_rounds}\n",
        ]
        if state["review_feedback"] is not None:
            comment_parts.append(f"\n### Review Failures\n{state['review_feedback']}\n")
        if state["ci_summary"] is not None:
            comment_parts.append(f"\n### CI Summary\n{state['ci_summary']}\n")

        comment_body = await self._gated(
            content="".join(comment_parts),
            visibility=state["repo_visibility"],
            shape=WriterShape.PROSE,
            writer_name="pr_comment",
        )

        try:
            await pr_creator.comment_on_pr(
                repo_url=repo_url,
                pr_number=pr_number,
                body=comment_body,
            )
        except Exception as exc:
            await self._log.aerror(
                "comment_failure_failed",
                error=str(exc),
            )

        return {}

    async def _complete_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Emit the final WorkflowCompleteEvent."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        writer(
            WorkflowCompleteEvent(
                feature_branch=state["feature_branch"],
                ralph_branch=state["ralph_branch"],
                total_iterations=state["total_iterations"],
                accepted=gate_cleared(state["accept_verdict"]),
                outcome=classify_outcome(state),
                merged=state["merged"],
                final_commit_sha=state["feature_tip_sha"],
                error=state["merge_error"],
                pr_url=state["pr_url"],
                pr_number=state["pr_number"],
                ci_passed=state["ci_passed"],
                trajectory=state["trajectory"],
                criteria_validation=state["criteria_validation"],
            )
        )

        if gate_cleared(state["accept_verdict"]) and state["merged"]:
            await self._log.ainfo(
                "backup_cleanup_starting",
                prefix=state["feature_branch"],
            )
            await self._merger.cleanup_backup_branches(
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                prefix=state["feature_branch"],
                cache_key=ctx.cache_key,
            )
        else:
            await self._log.adebug(
                "backup_cleanup_skipped",
                accept_verdict=state["accept_verdict"],
                merged=state["merged"],
            )

        return {}
