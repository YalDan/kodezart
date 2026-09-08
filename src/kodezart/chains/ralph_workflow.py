"""Shared fire graph: generation, implementation, consolidation and review."""

import uuid
from collections.abc import AsyncIterator
from typing import Final

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
    EVAL_TOOLS,
    EVAL_TOOLS_WITH_AGENT,
)
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    BranchMerger,
    GitService,
    OutboundContentGate,
    PromptSetProvider,
    QualityGate,
    RefPublisher,
    Remediator,
    RepoCache,
    RepoVisibilityResolver,
    TicketGenerator,
)
from kodezart.core.redispatch import (
    correction_notice,
    correction_report,
    until_conforming,
    until_permutation,
)
from kodezart.core.retry import DelayFloor, RetryFloor, should_retry
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import (
    flagged_items,
    gate_cleared,
    sherlock_items,
)
from kodezart.domain.agent import best_iteration_ref, generate_ralph_branch_name
from kodezart.domain.base_scope import scope_base
from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import (
    demands_regeneration,
    regeneration_targets,
    sweep,
)
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.criteria_prompt import render_validation_findings
from kodezart.domain.errors import (
    CriteriaFanInError,
    ScopedExecutionUnavailableError,
    UngroundedVerdictError,
)
from kodezart.domain.fan_in import fan_in_report, require_permutation
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.outcome import classify_outcome
from kodezart.domain.prompt_variables import changeset_variables
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.domain.ticket import format_fire_spec, format_ticket_as_task
from kodezart.domain.trajectory import landable_commit
from kodezart.domain.workflow_state import (
    current_ticket,
    original_ticket,
    validated_artifact,
    validated_criteria,
)
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    BRANCH_NAME_SCHEMA,
    CRITERIA_VALIDATION_SCHEMA,
    GENERATED_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    BranchNameOutput,
    GeneratedCriteriaOutput,
    TicketDraftOutput,
    WorkflowArtifactsEvent,
    WorkflowCompleteEvent,
    WorkflowConsolidationEvent,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
    WorkflowIterationEvent,
    WorkflowRemediationEvent,
    WorkflowReviewEvent,
    WorkflowScopeBaseEvent,
    WorkflowTicketEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.consolidation import ConsolidationStatus
from kodezart.types.domain.criteria import (
    CriteriaValidationOutput,
    FanInReport,
    ValidatedCriterion,
)
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.workflow import (
    ExecutionContext,
    RemediationRequest,
    WorkflowState,
)

#: The refusals the criteria-validation guard answers with a re-dispatch.
#:
#: All three are refusals of a response the model can correct once it is
#: told what was refused, and none is transient — the graph's retry
#: predicate returns False for every one of them, which is why a node
#: retry re-runs the session and then ends the run.
CORRECTABLE_VALIDATION_BREACHES: Final[tuple[type[Exception], ...]] = (
    ValidationError,
    UngroundedVerdictError,
    CriteriaFanInError,
)


class RalphWorkflowEngine:
    """Shared fire graph: author, implement, consolidate and review."""

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
        ref_publisher: RefPublisher | None = None,
        remediator: Remediator | None = None,
        remediation_max_rounds: int,
        criteria_max_regeneration_rounds: int,
        fan_in_max_attempts: int,
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
        self._prompts: PromptSetProvider = prompts
        self._skills: SkillsSelection = skills
        self._gate: OutboundContentGate = gate
        self._visibility_resolver: RepoVisibilityResolver | None = visibility_resolver
        self._ref_publisher: RefPublisher | None = ref_publisher
        self._remediator: Remediator | None = remediator
        self._remediation_max_rounds: int = remediation_max_rounds
        self._criteria_max_regeneration_rounds: int = criteria_max_regeneration_rounds
        self._fan_in_max_attempts: int = fan_in_max_attempts
        self._artifact_persister: ArtifactPersister | None = artifact_persister
        self._retry: RetryPolicy = RetryPolicy(
            max_attempts=retry_max_attempts,
            initial_interval=retry_initial_interval,
            retry_on=should_retry,
        )
        self._floor: RetryFloor = RetryFloor(delay_floor_for)
        self._log: BoundLogger = get_logger(__name__)
        self._checkpointer: BaseCheckpointSaver[str] | None = checkpointer
        self._compiled = self._build_graph().compile(
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
        """Execute a fire and expose its delivery-free terminal."""
        initial_state, config = self._prepare(
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
        terminal: WorkflowCompleteEvent | None = None
        async for event in self._compiled.astream(
            initial_state,
            config=config,
            stream_mode="custom",
        ):
            if not isinstance(event, AgentEvent):
                raise TypeError(f"Expected AgentEvent, got {type(event).__name__}")
            if isinstance(event, WorkflowCompleteEvent):
                terminal = event
            yield event
        if terminal is None:
            raise RuntimeError("Fire graph emitted no terminal")
        await self._cleanup_backups(terminal, config)

    def _build_graph(
        self,
    ) -> StateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
        graph: StateGraph[WorkflowState, None, WorkflowState, WorkflowState] = (
            StateGraph(WorkflowState)
        )
        graph.add_node(
            "resolve_visibility",
            self._floor(self._resolve_visibility_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_branch",
            self._floor(self._generate_branch_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_ticket",
            self._floor(self._generate_ticket_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "generate_criteria",
            self._floor(self._generate_criteria_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "validate_criteria",
            self._floor(self._validate_criteria_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "run_ralph_loop",
            self._floor(self._run_ralph_loop_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "merge_to_feature",
            self._floor(self._merge_to_feature_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "land_best_iteration",
            self._floor(self._land_best_iteration_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "review_against_ticket",
            self._floor(self._review_against_ticket_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "remediate",
            self._floor(self._remediate_node),
            retry_policy=self._retry,
        )
        graph.add_node("complete", self._complete_node)

        if self._artifact_persister is not None:
            graph.add_node(
                "persist_ticket",
                self._floor(self._persist_ticket_node),
                retry_policy=self._retry,
            )
            graph.add_node(
                "persist_artifacts",
                self._floor(self._persist_artifacts_node),
                retry_policy=self._retry,
            )

        graph.add_conditional_edges(
            START,
            self._route_entry,
            {
                "resolve_visibility": "resolve_visibility",
                "generate_criteria": "generate_criteria",
            },
        )
        graph.add_edge("resolve_visibility", "generate_branch")
        graph.add_edge("generate_branch", "generate_ticket")
        if self._artifact_persister is not None:
            graph.add_edge("generate_ticket", "persist_ticket")
            graph.add_edge("persist_ticket", "generate_criteria")
        else:
            graph.add_edge("generate_ticket", "generate_criteria")
        graph.add_edge("generate_criteria", "validate_criteria")
        proceed = (
            "persist_artifacts"
            if self._artifact_persister is not None
            else "run_ralph_loop"
        )
        graph.add_conditional_edges(
            "validate_criteria",
            self._route_after_validation,
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
            {
                "review_against_ticket": "review_against_ticket",
                "remediate": "remediate",
                "land_best_iteration": "land_best_iteration",
                "complete": "complete",
            },
        )
        graph.add_edge("land_best_iteration", "complete")
        graph.add_conditional_edges(
            "review_against_ticket",
            self._route_after_review,
            {"remediate": "remediate", "complete": "complete"},
        )
        graph.add_edge("remediate", "generate_criteria")
        graph.add_edge("complete", END)
        return graph

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
                base_branch=ctx.base_spec.base_branch,
                base_role=ctx.base_spec.base_role,
                inputs=list(ctx.base_spec.inputs),
            )
        )
        await self._log.ainfo(
            "scope_base_resolved",
            base_branch=ctx.base_spec.base_branch,
            base_role=(
                None
                if ctx.base_spec.base_role is None
                else ctx.base_spec.base_role.value
            ),
            input_count=len(ctx.base_spec.inputs),
        )
        return {"repo_visibility": visibility}

    async def _gated(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> str:
        """Route one outbound payload through the one gated-write path."""
        return await gated_write(
            gate=self._gate,
            log=self._log,
            content=content,
            visibility=visibility,
            shape=shape,
            destination=destination,
            content_class=content_class,
        )

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
                skills=self._prompts.session_skills(
                    PromptKey.BRANCH_NAME, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                run_identity=ctx.run_identity,
                session_policy=self._prompts.session_policy(PromptKey.BRANCH_NAME),
                output_format={
                    "type": "json_schema",
                    "schema": BRANCH_NAME_SCHEMA,
                },
                cache_key=ctx.cache_key,
            ),
            site="branch_name",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for branch name"
            raise soft_failure(
                msg,
                raise_site="branch_name",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        output = BranchNameOutput.model_validate(result_event.structured_output)
        # AUTHORED: a model's summary of the raw task text. BRANCH_NAME is a
        # mandatory destination, so the class does not change the routing.
        slug = await self._gated(
            content=output.slug,
            visibility=state["repo_visibility"],
            shape=WriterShape.IDENTIFIER,
            destination=OutboundDestination.BRANCH_NAME,
            content_class=ContentClass.AUTHORED,
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
            run_identity=ctx.run_identity,
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

        ticket = current_ticket(state)

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
                skills=self._prompts.session_skills(
                    PromptKey.ACCEPTANCE_CRITERIA, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                run_identity=ctx.run_identity,
                # Generative: the set's lenses are dispatchable from here.
                agents=self._prompts.definitions(),
                session_policy=self._prompts.session_policy(
                    PromptKey.ACCEPTANCE_CRITERIA,
                ),
                output_format={
                    "type": "json_schema",
                    "schema": GENERATED_CRITERIA_SCHEMA,
                },
                cache_key=ctx.cache_key,
            ),
            site="acceptance_criteria",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for acceptance criteria"
            raise soft_failure(
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

        The refuter reports a verdict per criterion with its evidence;
        :func:`sweep` reconciles the report against the dispatched ids and
        grounds every stated verdict in the evidence beside it.  A set that
        still demands regeneration once the regeneration bound is spent
        halts the run here — before the loop, with the sweep as its report.

        The whole sweep is inside the re-dispatch guard, so a refusal it
        raises is answerable rather than fatal: the session is told what
        was refused and asked again under the same bound.  A refusal still
        standing when the bound is spent ends the run on that refusal.
        This channel has no fail-closed arm and none is invented here — a
        feasibility verdict nothing derived is exactly what the sweep
        refuses, so the refusal is what reaches the wire.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket = current_ticket(state)

        criteria = state["acceptance_criteria"]
        prompt = self._prompts.template_for(PromptKey.CRITERIA_VALIDATION).render(
            {
                "task_description": format_ticket_as_task(ticket),
                "acceptance_criteria": criteria,
                "base_ref": ctx.base_branch,
            },
        )

        async def validate(breach: Exception | None) -> CriteriaValidationOutput:
            notice = None if breach is None else correction_notice(breach)
            result_event, rate_limit_rejected = await drain(
                self._service.stream(
                    prompt=prompt if notice is None else f"{prompt}\n\n{notice}",
                    repo_path=ctx.repo_path,
                    repo_url=ctx.repo_url,
                    branch=ctx.base_branch,
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=EVAL_TOOLS,
                    skills=self._prompts.session_skills(
                        PromptKey.CRITERIA_VALIDATION, self._skills
                    ),
                    session_type=SessionType.TICKET_FIRE,
                    run_identity=ctx.run_identity,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(
                        PromptKey.CRITERIA_VALIDATION,
                    ),
                    output_format={
                        "type": "json_schema",
                        "schema": CRITERIA_VALIDATION_SCHEMA,
                    },
                    cache_key=ctx.cache_key,
                ),
                site="criteria_validation",
            )

            if result_event is None or result_event.structured_output is None:
                msg = "Agent did not produce structured output for criteria validation"
                raise soft_failure(
                    msg,
                    raise_site="criteria_validation",
                    result_event=result_event,
                    rate_limit_rejected=rate_limit_rejected,
                )

            return CriteriaValidationOutput.model_validate(
                result_event.structured_output,
            )

        redispatched = await until_conforming(
            dispatch=validate,
            check=lambda candidate: sweep(criteria, candidate),
            correctable=CORRECTABLE_VALIDATION_BREACHES,
            max_attempts=self._fan_in_max_attempts,
            site="criteria_validation",
            log=self._log,
        )
        if redispatched.unresolved is not None:
            raise redispatched.unresolved
        validation = sweep(criteria, redispatched.output)
        correction = correction_report(redispatched)
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
                correction=correction,
            )
        )
        if correction is not None:
            await self._log.awarning(
                "criteria_contract_correction",
                site="criteria_validation",
                attempts=correction.attempts,
                breach_classes=[b.breach_class for b in correction.breaches],
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

    def _route_after_validation(self, state: WorkflowState) -> str:
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
        work_base_ref: str,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[ValidatedCriterion],
        cache_key: str,
        run_identity: RunIdentity | None = None,
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
            work_base_ref=work_base_ref,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            acceptance_criteria=acceptance_criteria,
            cache_key=cache_key,
            run_identity=run_identity,
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
        """Delegate to the quality gate for iterative execution.

        The round's ticket and the ref its branch is cut from arrive the
        same way — off the state a remediation round wrote — so a round
        implementing a fix is built on the tree that fix is about.
        """
        ctx = ExecutionContext.from_configurable(config)

        ticket = current_ticket(state)

        implementation_prompt = self._prompts.template_for(
            PromptKey.IMPLEMENTATION,
        ).render({"task_md": format_fire_spec(AuthoredSpec(ticket=ticket))})

        last_iteration_event = await self._run_quality_gate(
            prompt=implementation_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            feature_branch=state["feature_branch"],
            ralph_branch=state["ralph_branch"],
            base_spec=ctx.base_spec,
            work_base_ref=state["work_base_ref"],
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            acceptance_criteria=validated_criteria(state),
            cache_key=ctx.cache_key,
            run_identity=ctx.run_identity,
            repo_visibility=state["repo_visibility"],
        )

        # feature_tip_sha is left None here; _merge_to_feature_node sets it
        # from the merger's outcome.feature_tip_sha (canonical, post-push).
        return {
            "accept_verdict": last_iteration_event.verdict,
            "flagged_items": flagged_items(
                validated_criteria(state),
                last_iteration_event.evaluation.criteria_results,
                last_iteration_event.evaluation.sherlock_flags,
            ),
            # A SUM, not a replacement: a remediation round runs its own
            # loop, and a terminal reporting only the last round's count
            # would understate what the run actually spent.
            "total_iterations": (
                state["total_iterations"] + last_iteration_event.iteration
            ),
            "feature_tip_sha": None,
            "trajectory": last_iteration_event.trajectory,
        }

    async def _persist_ticket_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Persist the finished ticket the moment it exists, before criteria.

        The drafter's output — draft plus its review rounds — is the most
        expensive artefact the pipeline produces, and it lived only in
        graph state until a node downstream of criteria generation wrote
        it.  A transient failure at criteria generation therefore
        discarded a finished, reviewed ticket and forced a re-run to
        redraft from scratch.  Writing it here bounds that loss to the
        node that actually failed.

        The criteria are NOT written here — they do not exist yet, which
        is the whole reason the combined write sat downstream.
        """
        ctx = ExecutionContext.from_configurable(config)

        if self._artifact_persister is None:
            msg = "persist_ticket node requires artifact_persister"
            raise RuntimeError(msg)

        ticket: TicketDraftOutput = current_ticket(state)

        await self._artifact_persister.persist(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["ralph_branch"],
            base_branch=state["work_base_ref"],
            artifacts={
                "ticket.json": await self._gated(
                    content=ticket.model_dump_json(indent=2, by_alias=True),
                    visibility=state["repo_visibility"],
                    shape=WriterShape.PROSE,
                    destination=OutboundDestination.ARTIFACT_TICKET_JSON,
                    content_class=ContentClass.AUTHORED,
                ),
            },
            cache_key=ctx.cache_key,
        )
        await self._log.ainfo(
            "ticket_persisted",
            branch=state["ralph_branch"],
            title=ticket.title,
        )
        return {}

    async def _persist_artifacts_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Persist ticket and criteria to .kodezart/ on the ralph branch.

        Writes the ticket again rather than only the criteria: a
        remediation round replaces the run's working ticket, and this is
        the write that reaches the branch after that happens.  A branch
        carrying the original ticket while the loop implements the
        remediation one would be a document that contradicts the work.

        This node CREATES the round's ralph branch when it runs, before
        the loop's first iteration ever asks for it, so it cuts from the
        run's work base for the same reason the loop does — a branch cut
        here from anywhere else is the base the loop would inherit.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if self._artifact_persister is None:
            msg = "persist_artifacts node requires artifact_persister"
            raise RuntimeError(msg)

        ticket: TicketDraftOutput = current_ticket(state)

        criteria_artifact = validated_artifact(state)

        # AUTHORED, both: a JSON container does not make its leaves derived,
        # and every leaf here was written by the model.
        artifacts: dict[str, str] = {
            "ticket.json": await self._gated(
                content=ticket.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.ARTIFACT_TICKET_JSON,
                content_class=ContentClass.AUTHORED,
            ),
            "criteria.json": await self._gated(
                content=criteria_artifact.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.ARTIFACT_CRITERIA_JSON,
                content_class=ContentClass.AUTHORED,
            ),
        }

        status = await self._artifact_persister.persist(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["ralph_branch"],
            base_branch=state["work_base_ref"],
            artifacts=artifacts,
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowArtifactsEvent(
                status=status,
                branch=state["ralph_branch"],
            )
        )
        # TODO(artifact-resume): On checkpoint resume, check if artifacts
        # already exist on the branch and skip regeneration. Requires the
        # HTTP→handler→engine thread_id plumbing in ralph_workflow.py:130-145.
        return {}

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

        This is the ONLY writer of ``work_base_ref``, and it writes it on
        exactly the two statuses that put work on the feature branch.
        Every other arm omits the key, leaving the ref a later round
        stands on where the last integration left it.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if not gate_cleared(state["accept_verdict"]):
            trajectory = state["trajectory"]
            best = None if trajectory is None else landable_commit(trajectory)
            exit_state: dict[str, object] = {
                "merged": False,
                "merge_error": None,
                "feature_tip_sha": None,
                "review_base_sha": None,
                "review_head_sha": None,
            }
            # A round that committed nothing writes nothing: omitting the
            # key leaves the previously recorded best standing, so a run
            # whose LAST round was empty is not reported as having done
            # no work at all.
            if best is not None:
                exit_state["best_iteration_sha"] = best
            return exit_state

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
            "work_base_ref": state["feature_branch"],
        }

    def _route_after_merge(self, state: WorkflowState) -> str:
        """Only review merged code; land what a loop exit produced.

        The three arms are the three ways the consolidation node can end.
        Unmerged WITH an error is a divergent accepted run — its work is
        already on the feature branch and the terminal reports the
        divergence.  Unmerged with NO error is the loop exit, which is
        the run this lane exists to stop stranding.
        """
        if state["merged"]:
            return "review_against_ticket"
        if state["merge_error"] is not None:
            return "complete"
        if self._rounds_remain(state):
            return "remediate"
        return "land_best_iteration"

    async def _land_best_iteration_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Publish and select the best available work without opening a PR."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        trajectory = state["trajectory"]
        best_sha = state["best_iteration_sha"]
        if trajectory is None or best_sha is None:
            await self._log.ainfo(
                "stall_exit_no_commit_to_land",
                feature_branch=state["feature_branch"],
                total_iterations=state["total_iterations"],
            )
            return {}

        if self._ref_publisher is None or ctx.repo_url is None:
            return {}

        best_ref = best_iteration_ref(state["feature_branch"])
        await self._ref_publisher.publish(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            commit_sha=best_sha,
            ref=best_ref,
            cache_key=ctx.cache_key,
        )

        outcome = await self._merger.consolidate(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=ctx.base_branch,
            feature_branch=state["feature_branch"],
            source_branch=best_ref,
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowConsolidationEvent(
                status=outcome.status,
                feature_branch=state["feature_branch"],
                source_branch=best_ref,
                feature_tip_sha=outcome.feature_tip_sha,
            )
        )
        integrated = outcome.status in (
            ConsolidationStatus.FAST_FORWARDED,
            ConsolidationStatus.ALREADY_INTEGRATED,
        )
        head = state["feature_branch"] if integrated else best_ref
        head_sha = outcome.feature_tip_sha if integrated else best_sha

        return {"feature_branch": head, "feature_tip_sha": head_sha}

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
                "criteria": validated_criteria(state),
                **changeset_variables(changeset),
            },
        )

        async def review() -> AcceptanceCriteriaOutput:
            result_event, rate_limit_rejected = await drain(
                self._service.stream(
                    prompt=prompt,
                    repo_path=ctx.repo_path,
                    repo_url=ctx.repo_url,
                    branch=state["feature_branch"],
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=EVAL_TOOLS,
                    skills=self._prompts.session_skills(
                        PromptKey.POST_MERGE_REVIEW, self._skills
                    ),
                    session_type=SessionType.TICKET_FIRE,
                    run_identity=ctx.run_identity,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(
                        PromptKey.POST_MERGE_REVIEW,
                    ),
                    output_format={
                        "type": "json_schema",
                        "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                    },
                    cache_key=ctx.cache_key,
                ),
                site="post_merge_review",
            )

            if result_event is None or result_event.structured_output is None:
                msg = "Agent did not produce structured output for review"
                raise soft_failure(
                    msg,
                    raise_site="post_merge_review",
                    result_event=result_event,
                    rate_limit_rejected=rate_limit_rejected,
                )

            return AcceptanceCriteriaOutput.model_validate(
                result_event.structured_output,
            )

        criteria = validated_criteria(state)
        output, unresolved, attempts = await until_permutation(
            dispatch=review,
            check=lambda candidate: require_permutation(
                grade_iteration(criteria, candidate),
            ),
            max_attempts=self._fan_in_max_attempts,
            site="post_merge_review",
            log=self._log,
        )
        grade = grade_iteration(criteria, output)
        fan_in: FanInReport | None = None
        if unresolved is not None:
            # Same guard, same exhaustion arm as the loop's evaluator —
            # a separate call site, so it is wired and asserted separately
            # rather than assumed to inherit anything.
            fan_in = fan_in_report(grade, attempts=attempts)
            await self._log.awarning(
                "fan_in_exhausted",
                site="post_merge_review",
                # No loop here, which is a STATE of this event and not a
                # field to leave out: one event name reaching a reader in
                # two shapes is two events wearing one name.
                iteration=None,
                attempts=attempts,
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
                fix_rounds_used=state["remediation_rounds_used"],
                fan_in=fan_in,
            )
        )

        # The reviewer's own concerns ride to the pull request the prompt
        # promises them to.  They are appended, not substituted: the loop's
        # flagged items describe the work, these describe the review of it.
        return {
            "review_passed": passed,
            "review_feedback": feedback,
            "flagged_items": [
                *state["flagged_items"],
                *sherlock_items(grade.sherlock_flags),
            ],
        }

    def _route_after_review(self, state: WorkflowState) -> str:
        if state["review_passed"]:
            return "complete"
        if self._rounds_remain(state):
            return "remediate"
        return "complete"

    def _rounds_remain(self, state: WorkflowState) -> bool:
        """Whether the run may spend another remediation round.

        ONE counter, read by all three routes.  Two counters would make
        the worst case twice the budget, and the routes are not
        independent — a remediation loop that ends unaccepted and then
        opens a request whose CI fails is one run failing twice, not two
        separate failures.
        """
        return (
            self._remediator is not None
            and state["remediation_rounds_used"] < self._remediation_max_rounds
        )

    async def _remediate_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        entry = self._remediation_entry(state)
        work_base_ref = state["work_base_ref"]
        request = RemediationRequest(
            entry=entry,
            round_index=state["remediation_rounds_used"],
            original_ticket=original_ticket(state),
            work_branch=state["feature_branch"],
            work_base_ref=work_base_ref,
            total_iterations=state["total_iterations"],
            trajectory=state["trajectory"],
            criteria=validated_criteria(state),
            failure_evidence=self._failure_evidence(state, entry),
        )

        return await self._draft_remediation(state, request, config)

    def _remediation_entry(self, state: WorkflowState) -> RemediationEntry:
        """Which failure opened this round — computed from state, not routing.

        The three routes share a join point, so the node a run arrived
        from carries less information than the state it arrived with —
        the same reason the terminal outcome is computed rather than
        judged from routing provenance.
        """
        if state["merged"] and state["review_passed"] is False:
            return RemediationEntry.review_failure
        return RemediationEntry.loop_not_accepted

    def _failure_evidence(
        self,
        state: WorkflowState,
        entry: RemediationEntry,
    ) -> str:
        """The evidence for the entry that fired, never a generic summary."""
        if entry is RemediationEntry.review_failure:
            return (
                state["review_feedback"]
                or "The post-merge review rejected the work with no feedback."
            )
        trajectory = state["trajectory"]
        if trajectory is None:
            return "The loop ended without acceptance and recorded no iterations."
        never_passed = ", ".join(trajectory.never_passed_ids) or "none"
        plateau = "; the run plateaued" if trajectory.plateaued else ""
        return (
            "The loop ended without acceptance after "
            f"{state['total_iterations']} iterations. Best pass count "
            f"{trajectory.best_passed_count} at iteration "
            f"{trajectory.best_iteration}{plateau}. "
            f"Criteria that passed in no iteration: {never_passed}."
        )

    async def _complete_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Emit the fire terminal before external delivery."""
        _ = config
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
                merge_error=state["merge_error"],
                trajectory=state["trajectory"],
                criteria_validation=state["criteria_validation"],
            )
        )

        return {}

    def _prepare(
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
    ) -> tuple[WorkflowState, RunnableConfig]:
        """Execute the full workflow pipeline.

        *base_spec* is the lane's recorded base and *implied_base* is the
        base its blockers imply now; the run refuses before any node when
        they differ, because a criterion graded against a base that has
        moved is about a tree that no longer exists.

        ``cache_key`` IS the LangGraph thread id: the caller's job id
        addresses this run's checkpoints.
        """
        if scope is not None:
            msg = "Scoped execution requires the scope entry pipeline"
            raise ScopedExecutionUnavailableError(msg, ref=scope)
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
            run_identity=run_identity,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = workflow_thread_id(cache_key)

        config: RunnableConfig = {"configurable": configurable}

        initial_state: WorkflowState = {
            "issue_key": issue_key,
            "feature_branch": "",
            "ralph_branch": "",
            "work_base_ref": base_spec.base_branch,
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
            "remediation_rounds_used": 0,
            "remediation_ticket": None,
            "remediation_entry": None,
            "best_iteration_sha": None,
            "repo_url": resolved_url,
            "repo_visibility": RepoVisibility.UNKNOWN,
            "trajectory": None,
        }

        return initial_state, config

    def _route_entry(self, state: WorkflowState) -> str:
        """An explicit drafted remediation resumes at the shared criteria gate."""
        if state["remediation_ticket"] is not None:
            return "generate_criteria"
        return "resolve_visibility"

    async def _draft_remediation(
        self,
        state: WorkflowState,
        request: RemediationRequest,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        remediator = self._remediator
        if remediator is None:
            raise RuntimeError("Remediation requires a configured remediator")
        entry = request.entry
        work_base_ref = request.work_base_ref
        remediation_event: WorkflowRemediationEvent | None = None
        async for event in remediator.run(
            request,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            cache_key=ctx.cache_key,
            run_identity=ctx.run_identity,
        ):
            writer(event)
            if isinstance(event, WorkflowRemediationEvent):
                remediation_event = event

        if remediation_event is None:
            msg = "Remediator did not emit a WorkflowRemediationEvent."
            raise RuntimeError(msg)

        await self._log.ainfo(
            "remediation_round_opened",
            entry=entry.value,
            round_index=state["remediation_rounds_used"],
            base_ref=work_base_ref,
            rounds_remaining=(
                self._remediation_max_rounds - state["remediation_rounds_used"] - 1
            ),
        )
        return {
            "remediation_rounds_used": state["remediation_rounds_used"] + 1,
            "remediation_ticket": remediation_event.ticket,
            "remediation_entry": entry,
            "ralph_branch": generate_ralph_branch_name(state["feature_branch"]),
            "acceptance_criteria": [],
            "criteria_artifact": None,
            "criteria_validation": None,
            "criteria_regeneration_rounds": 0,
            "accept_verdict": AcceptVerdict.rejected,
            "review_passed": False,
            "merged": False,
            "merge_error": None,
        }

    async def _cleanup_backups(
        self,
        terminal: WorkflowCompleteEvent,
        config: RunnableConfig,
    ) -> None:
        ctx = ExecutionContext.from_configurable(config)
        if terminal.accepted and terminal.merged:
            await self._log.ainfo(
                "backup_cleanup_starting",
                prefix=terminal.feature_branch,
            )
            await self._merger.cleanup_backup_branches(
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                prefix=terminal.feature_branch,
                cache_key=ctx.cache_key,
            )
        else:
            await self._log.adebug(
                "backup_cleanup_skipped",
                accept_verdict=terminal.accepted,
                merged=terminal.merged,
            )
