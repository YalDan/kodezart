"""Ralph workflow engine — outer pipeline: branch generation, loop, post-merge."""

import uuid
from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import TypeAdapter

from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
    EVAL_TOOLS,
    EVAL_TOOLS_WITH_AGENT,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    BranchMerger,
    CIMonitor,
    PRCreator,
    QualityGate,
    TicketGenerator,
)
from kodezart.core.retry import should_retry
from kodezart.domain.agent import generate_ralph_branch_name
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.prompts import evaluation as evaluation_prompt
from kodezart.prompts import pr_description as pr_description_prompt
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    BRANCH_NAME_SCHEMA,
    GENERATED_CRITERIA_SCHEMA,
    PR_DESCRIPTION_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    BranchNameOutput,
    GeneratedCriteriaOutput,
    PRDescriptionOutput,
    ResultEvent,
    TicketDraftOutput,
    WorkflowCIEvent,
    WorkflowCompleteEvent,
    WorkflowCriteriaEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
    WorkflowReviewEvent,
    WorkflowTicketEvent,
)
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState

_CRITERIA_TA: TypeAdapter[list[str]] = TypeAdapter(list[str])


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
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int = 3,
        retry_initial_interval: float = 1.0,
        pr_creator: PRCreator | None = None,
        ci_monitor: CIMonitor | None = None,
        max_fix_rounds: int = 2,
        artifact_persister: ArtifactPersister | None = None,
    ) -> None:
        self._service: AgentRunner = service
        self._quality_gate: QualityGate = quality_gate
        self._ticket_generator: TicketGenerator = ticket_generator
        self._merger: BranchMerger = merger
        self._git_base_url: str = git_base_url
        self._pr_creator: PRCreator | None = pr_creator
        self._ci_monitor: CIMonitor | None = ci_monitor
        self._max_fix_rounds: int = max_fix_rounds
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
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
    ) -> AsyncIterator[AgentEvent]:
        """Execute the full workflow pipeline.

        Generate cache_key, resolve repo URL, build execution context, and
        stream events from the compiled LangGraph graph.
        """
        # TODO(time-travel): E2E checkpoint resume requires changes here
        # — this is the root of the resume chain (HTTP → handler →
        # protocol → here → sub-graphs).
        # 1. Accept optional thread_id param; reuse it as cache_key
        #    for resume instead of generating a fresh uuid.
        # 2. On resume: pass None (not initial_state) to astream()
        #    so LangGraph loads from the outer checkpoint.
        # 3. Emit cache_key in the first SSE event so callers can
        #    store it for future resume requests.
        # 4. Sub-graphs are called imperatively (not LangGraph
        #    subgraphs), so each has isolated checkpoints. On outer
        #    resume the sub-graph nodes re-enter; inner loops must
        #    also accept a resume signal (see ralph_loop.py and
        #    ticket_generation.py TODOs).
        # 5. WorkflowEngine protocol, WorkflowRequest, and handler
        #    all need a thread_id param to plumb resume from HTTP.
        cache_key = uuid.uuid4().hex

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
            base_branch=base_branch,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = cache_key

        config: RunnableConfig = {"configurable": configurable}

        initial_state: WorkflowState = {
            "feature_branch": "",
            "ralph_branch": "",
            "ticket": None,
            "acceptance_criteria": [],
            "accepted": False,
            "total_iterations": 0,
            "last_commit_sha": None,
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

        graph.add_edge(START, "generate_branch")
        graph.add_edge("generate_branch", "generate_ticket")
        graph.add_edge("generate_ticket", "generate_criteria")
        if self._artifact_persister is not None:
            graph.add_edge("generate_criteria", "persist_artifacts")
            graph.add_edge("persist_artifacts", "run_ralph_loop")
        else:
            graph.add_edge("generate_criteria", "run_ralph_loop")
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
        graph.add_edge("fix_code", "review_against_ticket")
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

    async def _generate_branch_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Ask the agent to generate a descriptive branch name."""
        _ = state  # required by LangGraph but unused in this node
        from kodezart.prompts import branch_name as branch_name_prompt

        ctx = ExecutionContext.from_configurable(config)
        result_event: ResultEvent | None = None
        async for event in self._service.stream(
            prompt=f"{branch_name_prompt.PROMPT}\n\nTask: {ctx.prompt}",
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=[],
            output_format={
                "type": "json_schema",
                "schema": BRANCH_NAME_SCHEMA,
            },
            cache_key=ctx.cache_key,
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for branch name"
            raise RuntimeError(msg)

        output = BranchNameOutput.model_validate(result_event.structured_output)
        feature_branch = f"kodezart/{output.slug}-{uuid.uuid4().hex[:8]}"
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
        from kodezart.prompts import acceptance_criteria as criteria_prompt

        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket = state["ticket"]
        if ticket is None:
            msg = "generate_criteria requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        prompt = criteria_prompt.build_prompt(
            task_description=format_ticket_as_task(ticket),
        )

        result_event: ResultEvent | None = None
        async for event in self._service.stream(
            prompt=prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=ctx.base_branch,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=EVAL_TOOLS_WITH_AGENT,
            output_format={
                "type": "json_schema",
                "schema": GENERATED_CRITERIA_SCHEMA,
            },
            cache_key=ctx.cache_key,
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for acceptance criteria"
            raise RuntimeError(msg)

        output = GeneratedCriteriaOutput.model_validate(
            result_event.structured_output,
        )

        writer(
            WorkflowCriteriaEvent(
                criteria=output.criteria,
                reasoning=output.reasoning,
            )
        )

        return {"acceptance_criteria": output.criteria}

    async def _run_ralph_loop_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Delegate to the quality gate for iterative execution."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ticket = state["ticket"]
        if ticket is None:
            msg = "run_ralph_loop requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        last_iteration_event: WorkflowIterationEvent | None = None
        async for event in self._quality_gate.run(
            prompt=format_ticket_as_task(ticket),
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            feature_branch=state["feature_branch"],
            ralph_branch=state["ralph_branch"],
            base_branch=ctx.base_branch,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            acceptance_criteria=state["acceptance_criteria"],
            cache_key=ctx.cache_key,
        ):
            writer(event)
            if isinstance(event, WorkflowIterationEvent):
                last_iteration_event = event

        if last_iteration_event is None:
            msg = "Ralph loop completed without emitting an iteration event."
            raise RuntimeError(msg)

        return {
            "accepted": last_iteration_event.accepted,
            "total_iterations": last_iteration_event.iteration,
            "last_commit_sha": last_iteration_event.commit_sha,
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

        artifacts: dict[str, str] = {
            "ticket.json": ticket.model_dump_json(indent=2, by_alias=True),
            "criteria.json": _CRITERIA_TA.dump_json(
                state["acceptance_criteria"],
                indent=2,
            ).decode(),
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
        """Merge ralph branch into feature branch (extracted from old finalize)."""
        ctx = ExecutionContext.from_configurable(config)
        merged_sha: str | None = None
        merge_error: str | None = None

        if state["accepted"] and state["last_commit_sha"] is not None:
            try:
                merged_sha = await self._merger.merge_and_push(
                    repo_path=ctx.repo_path,
                    repo_url=ctx.repo_url,
                    base_branch=ctx.base_branch,
                    feature_branch=state["feature_branch"],
                    source_branch=state["ralph_branch"],
                    cache_key=ctx.cache_key,
                )
            except Exception as exc:
                merge_error = str(exc)
                await self._log.aerror("merge_failed", error=merge_error)

            if merged_sha is not None:
                await self._merger.cleanup_source(
                    repo_path=ctx.repo_path,
                    repo_url=ctx.repo_url,
                    source_branch=state["ralph_branch"],
                    cache_key=ctx.cache_key,
                )

        return {
            "merged": merged_sha is not None,
            "merge_error": merge_error,
            "last_commit_sha": merged_sha or state["last_commit_sha"],
        }

    def _route_after_merge(self, state: WorkflowState) -> str:
        """Guard: only review merged code — skip to complete if not merged."""
        if state["merged"]:
            return "review_against_ticket"
        return "complete"

    async def _review_against_ticket_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Evaluate merged code against ticket acceptance criteria."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        prompt = evaluation_prompt.build_prompt(state["acceptance_criteria"])

        result_event: ResultEvent | None = None
        async for event in self._service.stream(
            prompt=prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["feature_branch"],
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
            msg = "Agent did not produce structured output for review"
            raise RuntimeError(msg)

        output = AcceptanceCriteriaOutput.model_validate(
            result_event.structured_output,
        )
        passed = all(r.passed for r in output.criteria_results)

        feedback: str | None = None
        if not passed:
            failures = [r for r in output.criteria_results if not r.passed]
            feedback = "\n".join(f"- {r.criterion}: {r.reasoning}" for r in failures)

        writer(
            WorkflowReviewEvent(
                passed=passed,
                evaluation=output,
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
        """Create a fix branch, run the agent, merge back into feature branch."""
        ctx = ExecutionContext.from_configurable(config)

        fix_branch = generate_ralph_branch_name(state["feature_branch"])

        ticket = state["ticket"]
        if ticket is None:
            msg = "fix_code requires a ticket but state['ticket'] is None."
            raise RuntimeError(msg)

        task_md = format_ticket_as_task(ticket)
        fix_prompt_parts = [f"Fix the following issues in the code:\n\n{task_md}"]
        if state["review_feedback"] is not None:
            fix_prompt_parts.append(
                f"\n\n## Review Failures\n{state['review_feedback']}"
            )
        if state["ci_summary"] is not None:
            fix_prompt_parts.append(f"\n\n## CI Failures\n{state['ci_summary']}")
        fix_prompt = "".join(fix_prompt_parts)

        async for _event in self._service.stream_workflow(
            prompt=fix_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=state["feature_branch"],
            branch_name=state["feature_branch"],
            ralph_branch=fix_branch,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            create_branch=True,
            cache_key=ctx.cache_key,
        ):
            pass  # consume stream

        merged_sha = await self._merger.merge_and_push(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=ctx.base_branch,
            feature_branch=state["feature_branch"],
            source_branch=fix_branch,
            cache_key=ctx.cache_key,
        )

        await self._merger.cleanup_source(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            source_branch=fix_branch,
            cache_key=ctx.cache_key,
        )

        return {
            "fix_rounds_used": state["fix_rounds_used"] + 1,
            "last_commit_sha": merged_sha or state["last_commit_sha"],
            "ci_passed": False,
            "ci_summary": None,
        }

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
        prompt = pr_description_prompt.build_prompt(
            ticket=ticket,
            acceptance_criteria=state["acceptance_criteria"],
            total_iterations=state["total_iterations"],
        )
        result_event: ResultEvent | None = None
        async for event in self._service.stream(
            prompt=prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=[],
            output_format={
                "type": "json_schema",
                "schema": PR_DESCRIPTION_SCHEMA,
            },
            cache_key=ctx.cache_key,
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for PR description"
            raise RuntimeError(msg)

        pr_output = PRDescriptionOutput.model_validate(
            result_event.structured_output,
        )

        pr_url, pr_number = await pr_creator.create_pr(
            repo_url=repo_url,
            title=pr_output.title,
            body=pr_output.description,
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

        comment_body = "".join(comment_parts)

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
                accepted=state["accepted"],
                merged=state["merged"],
                final_commit_sha=state["last_commit_sha"],
                error=state["merge_error"],
                pr_url=state["pr_url"],
                pr_number=state["pr_number"],
                ci_passed=state["ci_passed"],
            )
        )

        if state["accepted"] and state["merged"]:
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
                accepted=state["accepted"],
                merged=state["merged"],
            )

        return {}
