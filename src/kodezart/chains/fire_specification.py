"""Author the branch, ticket and feasible acceptance criteria."""

import uuid
from typing import Final

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import ValidationError

from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    AgentRunner,
    OutboundContentGate,
    PromptSetProvider,
    RepoVisibilityResolver,
    TicketGenerator,
)
from kodezart.core.redispatch import (
    correction_notice,
    correction_report,
    until_conforming,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.agent import generate_ralph_branch_name
from kodezart.domain.criteria import build_artifact, mint_criteria
from kodezart.domain.criteria_feasibility import (
    regeneration_targets,
    sweep,
)
from kodezart.domain.criteria_prompt import render_validation_findings
from kodezart.domain.errors import (
    CriteriaFanInError,
    UngroundedVerdictError,
)
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.domain.workflow_state import (
    current_ticket,
)
from kodezart.types.domain.agent import (
    BRANCH_NAME_SCHEMA,
    CRITERIA_VALIDATION_SCHEMA,
    GENERATED_CRITERIA_SCHEMA,
    BranchNameOutput,
    GeneratedCriteriaOutput,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
    WorkflowScopeBaseEvent,
    WorkflowTicketEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.criteria import (
    CriteriaValidationOutput,
)
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.workflow import (
    ExecutionContext,
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


class FireSpecification:
    """Author the branch, ticket and feasible acceptance criteria."""

    def __init__(
        self,
        *,
        service: AgentRunner,
        ticket_generator: TicketGenerator,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        visibility_resolver: RepoVisibilityResolver | None,
        criteria_max_regeneration_rounds: int,
        fan_in_max_attempts: int,
    ) -> None:
        self._service = service
        self._ticket_generator = ticket_generator
        self._prompts = prompts
        self._skills = skills
        self._gate = gate
        self._visibility_resolver = visibility_resolver
        self._criteria_max_regeneration_rounds = criteria_max_regeneration_rounds
        self._fan_in_max_attempts = fan_in_max_attempts
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    async def resolve_visibility(
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

    async def generate_branch(
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
        slug = await gated_write(
            gate=self._gate,
            log=self._log,
            content=output.slug,
            visibility=state["repo_visibility"],
            shape=WriterShape.IDENTIFIER,
            destination=OutboundDestination.BRANCH_NAME,
            content_class=ContentClass.AUTHORED,
        )
        feature_branch = f"kodezart/{slug}-{uuid.uuid4().hex[:8]}"
        ralph_branch = generate_ralph_branch_name(feature_branch)
        return {"feature_branch": feature_branch, "ralph_branch": ralph_branch}

    async def generate_ticket(
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

        return {"fire_spec": AuthoredSpec(ticket=ticket_event.ticket)}

    async def generate_criteria(
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
                allowed_tools=ToolPreset.DELEGATED_EVALUATION,
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

    async def validate_criteria(
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
                    allowed_tools=ToolPreset.EVALUATION,
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
            "criterion_set": build_artifact(criteria, validation),
            "criteria_regeneration_rounds": (
                rounds_used if bound_exhausted or not targets else rounds_used + 1
            ),
            "criteria_infeasible": bound_exhausted,
        }
