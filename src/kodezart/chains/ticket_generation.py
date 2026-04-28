"""Ticket generation loop — draft + review until approved or exhausted."""

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from kodezart.core.constants import EVAL_PERMISSION_MODE, TICKET_TOOLS
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, WorkspaceProvider
from kodezart.core.retry import should_retry
from kodezart.domain.errors import WorkspaceError
from kodezart.prompts.ticket_generation import (
    build_create_prompt,
    build_review_prompt,
    build_revision_prompt,
)
from kodezart.types.domain.agent import (
    TICKET_DRAFT_SCHEMA,
    TICKET_REVIEW_SCHEMA,
    AgentEvent,
    ErrorEvent,
    ResultEvent,
    TicketDraftOutput,
    TicketReviewOutput,
    WorkflowTicketDraftEvent,
    WorkflowTicketEvent,
    WorkflowTicketReviewEvent,
)
from kodezart.types.domain.workflow import TicketGenerationState, WorkflowContext


class TicketGenerationLoop:
    """Iterates ticket drafting and review until approved or max reviews.

    Graph: START -> create -> review -> [conditional: create or finalize] -> END
    """

    def __init__(
        self,
        service: AgentRunner,
        workspace: WorkspaceProvider,
        *,
        max_reviews: int = 2,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int = 3,
        retry_initial_interval: float = 1.0,
    ) -> None:
        self._service = service
        self._workspace = workspace
        self._max_reviews = max_reviews
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
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Execute the ticket generation loop.

        Acquire a shared workspace, run create/review iterations via the
        compiled LangGraph graph, and release the workspace in a finally block.
        """
        # TODO(time-travel): workspace is acquired here before the graph
        # and stored in frozen WorkflowContext configurable — NOT in
        # checkpointed state. On checkpoint resume the worktree is gone
        # (/tmp ephemeral) and session_ids reference dead cwd paths.
        # Fix requires:
        # 1. Re-derive workspace inside each node from configurable
        #    (repo_path, repo_url, cache_key) — do NOT checkpoint the
        #    path itself (it's a dead /tmp artifact on resume).
        #    WorkflowContext is frozen=True so ctx.workspace_path
        #    cannot be mutated; pass the path directly instead.
        # 2. Invalidate session_ids when workspace is re-acquired
        #    (is_new → session=None) — the old session's conversation
        #    history references files in the dead worktree.
        # 3. Accept resume flag from outer workflow; pass None instead
        #    of initial_state to astream() (see ralph_workflow.py TODO).
        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                ref="HEAD",
                cache_key=cache_key,
            )
        except WorkspaceError as exc:
            yield ErrorEvent(error=str(exc))
            return

        try:
            await self._log.ainfo(
                "ticket_loop_workspace_acquired",
                workspace_path=workspace_path,
                cache_key=cache_key,
            )
            ctx = WorkflowContext(
                prompt=prompt,
                repo_path=repo_path,
                repo_url=repo_url,
                cache_key=cache_key,
                workspace_path=workspace_path,
            )
            configurable: dict[str, object] = ctx.model_dump()
            if self._checkpointer is not None:
                configurable["thread_id"] = f"{cache_key}-ticket"

            config: RunnableConfig = {"configurable": configurable}

            initial_state: TicketGenerationState = {
                "draft_iteration": 0,
                "review_count": 0,
                "current_draft": None,
                "review_feedback": None,
                "review_suggestions": [],
                "approved": False,
                "creator_session_id": None,
                "reviewer_session_id": None,
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
        finally:
            try:
                await self._workspace.release(workspace_path)
            except Exception as cleanup_exc:
                await self._log.awarning(
                    "workspace_cleanup_failed",
                    error=str(cleanup_exc),
                )
            await self._log.ainfo(
                "ticket_loop_workspace_released",
                workspace_path=workspace_path,
            )

    async def _create_node(
        self,
        state: TicketGenerationState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = WorkflowContext.from_configurable(config)
        writer = get_stream_writer()
        iteration = state["draft_iteration"] + 1

        if iteration == 1:
            body = build_create_prompt(task=ctx.prompt)
        else:
            current_draft = state["current_draft"]
            review_feedback = state["review_feedback"]
            if current_draft is None or review_feedback is None:
                msg = "Revision requires a previous draft and review feedback."
                raise RuntimeError(msg)
            body = build_revision_prompt(
                task=ctx.prompt,
                previous_draft=current_draft,
                reviewer_feedback=review_feedback,
                reviewer_suggestions=state["review_suggestions"],
            )

        if ctx.workspace_path is None:
            msg = "workspace_path must be set before entering create node"
            raise RuntimeError(msg)

        result_event: ResultEvent | None = None
        async for event in self._service.stream_in_workspace(
            prompt=body,
            workspace_path=ctx.workspace_path,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=TICKET_TOOLS,
            output_format={
                "type": "json_schema",
                "schema": TICKET_DRAFT_SCHEMA,
            },
            session_id=state["creator_session_id"],
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Creator produced no structured output."
            raise RuntimeError(msg)

        draft = TicketDraftOutput.model_validate(
            result_event.structured_output,
        )
        writer(
            WorkflowTicketDraftEvent(iteration=iteration, draft=draft),
        )
        return {
            "draft_iteration": iteration,
            "current_draft": draft,
            "creator_session_id": result_event.session_id,
        }

    async def _review_node(
        self,
        state: TicketGenerationState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = WorkflowContext.from_configurable(config)
        writer = get_stream_writer()
        count = state["review_count"] + 1

        current_draft = state["current_draft"]
        if current_draft is None:
            msg = "Review requires a draft."
            raise RuntimeError(msg)

        body = build_review_prompt(task=ctx.prompt, draft=current_draft)

        if ctx.workspace_path is None:
            msg = "workspace_path must be set before entering review node"
            raise RuntimeError(msg)

        result_event: ResultEvent | None = None
        async for event in self._service.stream_in_workspace(
            prompt=body,
            workspace_path=ctx.workspace_path,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=TICKET_TOOLS,
            output_format={
                "type": "json_schema",
                "schema": TICKET_REVIEW_SCHEMA,
            },
            session_id=state["reviewer_session_id"],
        ):
            if isinstance(event, ResultEvent):
                result_event = event

        if result_event is None or result_event.structured_output is None:
            msg = "Reviewer produced no structured output."
            raise RuntimeError(msg)

        output = TicketReviewOutput.model_validate(
            result_event.structured_output,
        )
        writer(
            WorkflowTicketReviewEvent(
                iteration=count,
                approved=output.approved,
                feedback=output.feedback,
                suggestions=output.suggestions,
            ),
        )
        return {
            "review_count": count,
            "approved": output.approved,
            "review_feedback": output.feedback,
            "review_suggestions": output.suggestions,
            "reviewer_session_id": result_event.session_id,
        }

    async def _finalize_node(
        self,
        state: TicketGenerationState,
    ) -> dict[str, object]:
        writer = get_stream_writer()

        current_draft = state["current_draft"]
        if current_draft is None:
            msg = "Finalize requires a draft."
            raise RuntimeError(msg)

        writer(
            WorkflowTicketEvent(
                ticket=current_draft,
                review_rounds=state["review_count"],
                approved=state["approved"],
            ),
        )
        return {}

    def _should_continue(
        self,
        state: TicketGenerationState,
    ) -> str:
        if state["approved"] or state["review_count"] >= self._max_reviews:
            return "finalize"
        return "create"

    def _build_graph(
        self,
    ) -> StateGraph[
        TicketGenerationState, None, TicketGenerationState, TicketGenerationState
    ]:
        graph: StateGraph[
            TicketGenerationState,
            None,
            TicketGenerationState,
            TicketGenerationState,
        ] = StateGraph(TicketGenerationState)
        graph.add_node("create", self._create_node, retry_policy=self._retry)
        graph.add_node("review", self._review_node, retry_policy=self._retry)
        graph.add_node("finalize", self._finalize_node)
        graph.add_edge(START, "create")
        graph.add_edge("create", "review")
        graph.add_conditional_edges(
            "review",
            self._should_continue,
            ["create", "finalize"],
        )
        graph.add_edge("finalize", END)
        return graph
