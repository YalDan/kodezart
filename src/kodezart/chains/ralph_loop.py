"""Ralph quality-gating loop — execute + evaluate until accepted or exhausted."""

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptProvider,
    RepoCache,
)
from kodezart.core.retry import should_retry
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.prompt_variables import changeset_variables
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    ResultEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.trajectory import IterationRecord
from kodezart.types.domain.workflow import RalphLoopContext, RalphLoopState


class RalphLoop:
    """Iterates agent work until acceptance criteria pass or max iterations.

    Graph: START -> execute -> evaluate -> [conditional: execute or END]
    """

    def __init__(
        self,
        service: AgentRunner,
        *,
        max_iterations: int,
        plateau_window: int,
        git: GitService,
        cache: RepoCache,
        prompts: PromptProvider,
        skills: SkillsSelection,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int = 3,
        retry_initial_interval: float = 1.0,
    ) -> None:
        self._service = service
        self._max_iterations = max_iterations
        self._plateau_window = plateau_window
        # git/cache are injected solely so _evaluate_node can pre-compute
        # the ChangesetDigest passed to evaluation.build_prompt — the loop
        # does NOT take on canonical-tip bookkeeping (the outer engine's
        # merger does that internally).
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._prompts: PromptProvider = prompts
        self._skills: SkillsSelection = skills
        self._retry = RetryPolicy(
            max_attempts=retry_max_attempts,
            initial_interval=retry_initial_interval,
            retry_on=should_retry,
        )
        self._log: BoundLogger = get_logger(__name__)
        self._checkpointer = checkpointer
        self._compiled = self._build_graph().compile(
            checkpointer=self._checkpointer,
        )

    async def run(
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
    ) -> AsyncIterator[AgentEvent]:
        """Execute the quality-gating loop.

        Build ``RalphLoopContext`` from parameters, configure thread ID for
        checkpointing, and stream events from the compiled LangGraph graph.
        """
        ctx = RalphLoopContext(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            cache_key=cache_key,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            acceptance_criteria=acceptance_criteria,
            repo_visibility=repo_visibility,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = ralph_thread_id(cache_key)

        config: RunnableConfig = {"configurable": configurable}

        initial_state: RalphLoopState = {
            "iteration": 0,
            "verdict": AcceptVerdict.rejected,
            "pending_failures": [],
            "iteration_records": [],
        }

        # TODO(time-travel): For E2E checkpoint resume, two changes needed:
        # 1. Accept resume flag from outer workflow; pass None instead
        #    of initial_state to astream() so LangGraph loads from the
        #    ralph checkpoint ({cache_key}-ralph).
        # 2. Each iteration already acquires/releases a transient
        #    worktree within _execute_node (via stream_workflow →
        #    _run_in_workspace), so workspaces are self-contained per
        #    node — no cross-iteration workspace state to preserve.
        #    Session_id capture is NOT needed here: unlike ticket_
        #    generation, ralph loop has no multi-turn session continuity
        #    across iterations (each iteration is a fresh conversation).
        # See ralph_workflow.py TODO for the resume signal plumbing.
        async for event in self._compiled.astream(
            initial_state,
            config=config,
            stream_mode="custom",
        ):
            if not isinstance(event, AgentEvent):
                msg = f"Expected AgentEvent from stream, got {type(event).__name__}"
                raise TypeError(msg)
            yield event

    def _build_graph(
        self,
    ) -> StateGraph[RalphLoopState, None, RalphLoopState, RalphLoopState]:
        graph: StateGraph[RalphLoopState, None, RalphLoopState, RalphLoopState] = (
            StateGraph(RalphLoopState)
        )
        graph.add_node("execute", self._execute_node, retry_policy=self._retry)
        graph.add_node("evaluate", self._evaluate_node, retry_policy=self._retry)
        graph.add_edge(START, "execute")
        graph.add_edge("execute", "evaluate")
        graph.add_conditional_edges(
            "evaluate",
            self._should_continue,
            ["execute", END],
        )
        return graph

    async def _execute_node(
        self,
        state: RalphLoopState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = RalphLoopContext.from_configurable(config)
        writer = get_stream_writer()
        iteration = state["iteration"] + 1
        is_first = iteration == 1

        prompt = ctx.prompt
        if not is_first:
            prompt = self._prompts.template_for(PromptKey.ITERATION_FEEDBACK).render(
                {
                    "prior_prompt": prompt,
                    "pending_failures": state["pending_failures"],
                },
            )

        commit_sha: str | None = None
        async for event in self._service.stream_workflow(
            prompt=prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=(ctx.base_branch if is_first else ctx.ralph_branch),
            branch_name=ctx.feature_branch,
            ralph_branch=ctx.ralph_branch,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            skills=self._skills,
            visibility=ctx.repo_visibility,
            create_branch=is_first,
            cache_key=ctx.cache_key,
        ):
            writer(event)
            if isinstance(event, ResultEvent) and event.commit_sha:
                commit_sha = event.commit_sha

        return {
            "iteration": iteration,
            "iteration_commit_sha": commit_sha,
        }

    async def _evaluate_node(
        self,
        state: RalphLoopState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = RalphLoopContext.from_configurable(config)
        writer = get_stream_writer()
        cwd = (
            ctx.repo_path
            if ctx.repo_path is not None
            else await self._cache.ensure_available(
                ctx.repo_url or "",
                ctx.cache_key,
            )
        )
        changeset = await self._git.diff_summary(
            cwd=cwd,
            base_ref=ctx.base_branch,
            head_ref=ctx.ralph_branch,
        )
        eval_prompt = self._prompts.template_for(PromptKey.EVALUATION).render(
            {
                "criteria": ctx.acceptance_criteria,
                **changeset_variables(changeset),
            },
        )

        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=eval_prompt,
                repo_path=ctx.repo_path,
                repo_url=ctx.repo_url,
                branch=ctx.ralph_branch,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=EVAL_TOOLS,
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                },
                cache_key=ctx.cache_key,
            ),
            site="ralph_evaluator",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Evaluator produced no structured output."
            raise soft_failure(
                msg,
                raise_site="ralph_evaluator",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        output = AcceptanceCriteriaOutput.model_validate(
            result_event.structured_output,
        )
        grade = grade_iteration(ctx.acceptance_criteria, output)
        if grade.missing_ids or grade.unknown_ids or grade.duplicate_ids:
            await self._log.awarning(
                "evaluator_result_reconciliation",
                iteration=state["iteration"],
                dispatched_count=grade.dispatched_count,
                missing_ids=grade.missing_ids,
                unknown_ids=grade.unknown_ids,
                duplicate_ids=grade.duplicate_ids,
            )
        verdict = grade.verdict
        pending_failures = grade.failures
        reconciled = AcceptanceCriteriaOutput(
            criteria_results=grade.results,
            sherlock_flags=grade.sherlock_flags,
        )
        records = [
            *state["iteration_records"],
            IterationRecord(
                iteration=state["iteration"],
                passed_count=grade.passed_count,
                failing_criterion_ids=[f.criterion_id for f in pending_failures],
                commit_sha=state.get("iteration_commit_sha"),
            ),
        ]
        trajectory = fold_trajectory(records, plateau_window=self._plateau_window)
        writer(
            WorkflowIterationEvent(
                iteration=state["iteration"],
                branch=ctx.ralph_branch,
                commit_sha=state.get("iteration_commit_sha"),
                verdict=verdict,
                evaluation=reconciled,
                trajectory=trajectory,
            )
        )
        if (
            trajectory.plateaued
            and not gate_cleared(verdict)
            and state["iteration"] < self._max_iterations
        ):
            await self._log.awarning(
                "loop_plateau_stop",
                iterations_used=state["iteration"],
                iterations_remaining=self._max_iterations - state["iteration"],
                never_passed_ids=trajectory.never_passed_ids,
            )
        return {
            "verdict": verdict,
            "pending_failures": pending_failures,
            "iteration_records": records,
        }

    def _should_continue(
        self,
        state: RalphLoopState,
    ) -> str:
        if gate_cleared(state["verdict"]):
            return END
        if state["iteration"] >= self._max_iterations:
            return END
        trajectory = fold_trajectory(
            state["iteration_records"],
            plateau_window=self._plateau_window,
        )
        if trajectory.plateaued:
            return END
        return "execute"
