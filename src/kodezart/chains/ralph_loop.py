"""Ralph quality-gating loop — execute + evaluate until accepted or exhausted."""

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner
from kodezart.core.retry import should_retry
from kodezart.prompts import evaluation, iteration_feedback
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    CriterionResult,
    ResultEvent,
    WorkflowIterationEvent,
)
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
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int = 3,
        retry_initial_interval: float = 1.0,
    ) -> None:
        self._service = service
        self._max_iterations = max_iterations
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
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[str],
        cache_key: str,
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
            base_branch=base_branch,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            acceptance_criteria=acceptance_criteria,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = f"{cache_key}-ralph"

        config: RunnableConfig = {"configurable": configurable}

        initial_state: RalphLoopState = {
            "iteration": 0,
            "accepted": False,
            "pending_failures": [],
            "last_commit_sha": None,
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
            prompt = iteration_feedback.augment_prompt(
                prompt,
                state["pending_failures"],
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
            create_branch=is_first,
            cache_key=ctx.cache_key,
        ):
            writer(event)
            if isinstance(event, ResultEvent) and event.commit_sha:
                commit_sha = event.commit_sha

        return {
            "iteration": iteration,
            "last_commit_sha": commit_sha,
        }

    async def _evaluate_node(
        self,
        state: RalphLoopState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = RalphLoopContext.from_configurable(config)
        writer = get_stream_writer()
        eval_prompt = evaluation.build_prompt(ctx.acceptance_criteria)
        result_event: ResultEvent | None = None

        async for event in self._service.stream(
            prompt=eval_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=ctx.ralph_branch,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=EVAL_TOOLS,
            output_format={
                "type": "json_schema",
                "schema": ACCEPTANCE_CRITERIA_SCHEMA,
            },
            cache_key=ctx.cache_key,
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Evaluator produced no structured output."
            raise RuntimeError(msg)

        output = AcceptanceCriteriaOutput.model_validate(
            result_event.structured_output,
        )
        accepted = all(r.passed for r in output.criteria_results)
        pending_failures: list[CriterionResult] = [
            r for r in output.criteria_results if not r.passed
        ]
        writer(
            WorkflowIterationEvent(
                iteration=state["iteration"],
                branch=ctx.ralph_branch,
                commit_sha=state["last_commit_sha"],
                accepted=accepted,
                evaluation=output,
            )
        )
        return {
            "accepted": accepted,
            "pending_failures": pending_failures,
        }

    def _should_continue(
        self,
        state: RalphLoopState,
    ) -> str:
        if state["accepted"]:
            return END
        if state["iteration"] >= self._max_iterations:
            return END
        return "execute"
