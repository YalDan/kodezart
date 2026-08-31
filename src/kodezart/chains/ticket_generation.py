"""Ticket generation loop — draft + review until approved or exhausted."""

import sys
from collections.abc import AsyncIterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from kodezart.core.constants import EVAL_PERMISSION_MODE, TICKET_TOOLS
from kodezart.core.error_egress import build_error_event
from kodezart.core.errors import TicketReviewModeError, soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptSetProvider, WorkspaceProvider
from kodezart.core.retry import should_retry
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import WorkspaceError
from kodezart.domain.thread_id import ticket_thread_id
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import (
    TICKET_DRAFT_SCHEMA,
    TICKET_REVIEW_SCHEMA,
    AgentEvent,
    TicketDraftOutput,
    TicketReviewOutput,
    WorkflowTicketDraftEvent,
    WorkflowTicketEvent,
    WorkflowTicketReviewEvent,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import AgentDefinition
from kodezart.types.domain.ticket_review import (
    DEFAULT_MAX_REVIEWS,
    DRAFT_CRITIC_LENS,
    TicketApproval,
    TicketReviewMode,
)
from kodezart.types.domain.workflow import TicketGenerationState, WorkflowContext


def _refuse_an_unhonourable_mode(
    *,
    review_mode: TicketReviewMode,
    max_reviews: int | None,
    definitions: Sequence[AgentDefinition],
) -> None:
    """Raise when the configured mode's guarantee cannot be delivered.

    Both refusals are one defect wearing two faces: a knob is configured
    whose effect this composition cannot produce.  Under ``create_only`` a
    review budget bounds nothing that gets compiled — silently accepting it
    is how a deployment comes to believe it has two review rounds — and a
    resolved set with no draft critic leaves the single creator session
    with nothing checking it while the mode's contract says otherwise.
    """
    if review_mode is not TicketReviewMode.CREATE_ONLY:
        return
    if max_reviews is not None:
        msg = (
            "max_reviews is configured under a ticket review mode that "
            "compiles no review node for it to bound"
        )
        raise TicketReviewModeError(
            msg,
            settings=(
                f"ticket_review_mode={review_mode.value}",
                f"max_reviews={max_reviews}",
            ),
        )
    if not any(definition.name == DRAFT_CRITIC_LENS for definition in definitions):
        msg = (
            "the resolved prompt set declares no draft-critic lens, and it is "
            "the only review a ticket receives under this mode"
        )
        raise TicketReviewModeError(
            msg,
            settings=(
                f"ticket_review_mode={review_mode.value}",
                "prompt_set declares "
                + (
                    ", ".join(definition.name for definition in definitions)
                    or "no lens at all"
                ),
            ),
        )


class TicketGenerationLoop:
    """Iterates ticket drafting and review until approved or max reviews.

    Two graphs, chosen once at construction:

    * ``reviewed``    — START -> create -> review -> [create | finalize] -> END
    * ``create_only`` — START -> create -> finalize -> END

    The review node is not added and the conditional edge does not exist
    under ``create_only``.  A precondition belongs in routing rather than
    in a node body: a graph that compiles the reviewer and then asks each
    time whether to run it can still reach it, and "cannot happen" is a
    stronger statement than "does not happen".

    Under ``create_only`` the draft critic is dispatched inside the
    creator's own session and is the only review the ticket receives, so
    a set that declares no such lens is refused here rather than resolved
    into a run that reports success with nothing having checked it.

    NO node resumes a session.  Two runs died identically at the creator
    raise site because a revision resumed the draft session: the draft's
    background subagents append a task notification to the transcript
    AFTER the result, and a resumed CLI answers that dangling tail with a
    zero-API-call turn whose ``ResultMessage`` ends the SDK's receive
    loop before the revision prompt is dequeued.  The revision prompt
    already carries the full previous draft, the reviewer's feedback and
    every suggestion, and instructs the creator to re-investigate, so the
    resume bought nothing the prompt does not already hand over.
    """

    def __init__(
        self,
        service: AgentRunner,
        workspace: WorkspaceProvider,
        *,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        review_mode: TicketReviewMode,
        max_reviews: int | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int,
        retry_initial_interval: float,
    ) -> None:
        self._service = service
        self._workspace = workspace
        self._prompts: PromptSetProvider = prompts
        self._skills: SkillsSelection = skills
        self._review_mode = review_mode
        _refuse_an_unhonourable_mode(
            review_mode=review_mode,
            max_reviews=max_reviews,
            definitions=prompts.definitions(),
        )
        self._max_reviews = DEFAULT_MAX_REVIEWS if max_reviews is None else max_reviews
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
        base_branch: str,
    ) -> AsyncIterator[AgentEvent]:
        """Execute the ticket generation loop.

        Acquire a shared workspace, run create/review iterations via the
        compiled LangGraph graph, and release the workspace in a finally block.
        """
        # TODO(time-travel): workspace is acquired here before the graph
        # and stored in frozen WorkflowContext configurable — NOT in
        # checkpointed state. On checkpoint resume the worktree is gone
        # (/tmp ephemeral).
        # Fix requires:
        # 1. Re-derive workspace inside each node from configurable
        #    (repo_path, repo_url, cache_key) — do NOT checkpoint the
        #    path itself (it's a dead /tmp artifact on resume).
        #    WorkflowContext is frozen=True so ctx.workspace_path
        #    cannot be mutated; pass the path directly instead.
        # 2. Accept resume flag from outer workflow; pass None instead
        #    of initial_state to astream() (see ralph_workflow.py TODO).
        # The session-invalidation item this list used to carry is gone:
        # no node resumes a session, so none can reference a dead cwd.
        try:
            workspace_path = await self._workspace.acquire(
                repo_path=repo_path,
                repo_url=repo_url,
                ref=base_branch,
                cache_key=cache_key,
            )
        except WorkspaceError as exc:
            # ``exc_info=sys.exc_info()`` is passed explicitly to harden
            # against async-executor context loss (hynek/structlog#488
            # class).  Logging at exception level — never silently
            # downgrade a failed workspace acquire to a bare yielded
            # ``ErrorEvent`` with no log line.
            await self._log.aexception(
                "ticket_loop_workspace_acquire_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
                exc_info=sys.exc_info(),
            )
            yield build_error_event(exc)
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
                configurable["thread_id"] = ticket_thread_id(cache_key)

            config: RunnableConfig = {"configurable": configurable}

            initial_state: TicketGenerationState = {
                "draft_iteration": 0,
                "review_count": 0,
                "current_draft": None,
                "review_feedback": None,
                "review_suggestions": [],
                "approved": TicketApproval.NOT_REVIEWED,
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
            body = self._prompts.template_for(PromptKey.TICKET_CREATE).render(
                {"task": ctx.prompt},
            )
        else:
            current_draft = state["current_draft"]
            review_feedback = state["review_feedback"]
            if current_draft is None or review_feedback is None:
                msg = "Revision requires a previous draft and review feedback."
                raise RuntimeError(msg)
            suggestions = state["review_suggestions"]
            revision_variables: dict[str, object] = {
                "task": ctx.prompt,
                "previous_draft_md": format_ticket_as_task(current_draft),
                "reviewer_feedback": review_feedback,
                "reviewer_suggestions": suggestions,
            }
            if not suggestions:
                revision_variables["reviewer_suggestions_absent"] = True
            body = self._prompts.template_for(PromptKey.TICKET_REVISION).render(
                revision_variables,
            )

        if ctx.workspace_path is None:
            msg = "workspace_path must be set before entering create node"
            raise RuntimeError(msg)

        await self._log.ainfo(
            "ticket_create_attempt",
            iteration=iteration,
            resumed_session_id=None,
            prompt_chars=len(body),
            suggestion_count=len(state["review_suggestions"]),
        )
        result_event, rate_limit_rejected = await drain(
            self._service.stream_in_workspace(
                prompt=body,
                workspace_path=ctx.workspace_path,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=TICKET_TOOLS,
                skills=self._prompts.session_skills(
                    PromptKey.TICKET_CREATE
                    if iteration == 1
                    else PromptKey.TICKET_REVISION,
                    self._skills,
                ),
                session_type=SessionType.TICKET_FIRE,
                # Generative: the set's lenses are dispatchable from here.
                agents=self._prompts.definitions(),
                session_policy=self._prompts.session_policy(
                    PromptKey.TICKET_CREATE
                    if iteration == 1
                    else PromptKey.TICKET_REVISION,
                ),
                output_format={
                    "type": "json_schema",
                    "schema": TICKET_DRAFT_SCHEMA,
                },
                session_id=None,
            ),
            site="ticket_creator",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Creator produced no structured output."
            raise soft_failure(
                msg,
                raise_site="ticket_creator",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        draft = TicketDraftOutput.model_validate(
            result_event.structured_output,
        )
        writer(
            WorkflowTicketDraftEvent(iteration=iteration, draft=draft),
        )
        return {
            "draft_iteration": iteration,
            "current_draft": draft,
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

        body = self._prompts.template_for(PromptKey.TICKET_REVIEW).render(
            {
                "task": ctx.prompt,
                "draft_md": format_ticket_as_task(current_draft),
            },
        )

        if ctx.workspace_path is None:
            msg = "workspace_path must be set before entering review node"
            raise RuntimeError(msg)

        await self._log.ainfo(
            "ticket_review_attempt",
            iteration=count,
            resumed_session_id=None,
            prompt_chars=len(body),
            draft_iteration=state["draft_iteration"],
        )
        result_event, rate_limit_rejected = await drain(
            self._service.stream_in_workspace(
                prompt=body,
                workspace_path=ctx.workspace_path,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=TICKET_TOOLS,
                skills=self._prompts.session_skills(
                    PromptKey.TICKET_REVIEW, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                session_policy=self._prompts.session_policy(
                    PromptKey.TICKET_REVIEW,
                ),
                output_format={
                    "type": "json_schema",
                    "schema": TICKET_REVIEW_SCHEMA,
                },
                session_id=None,
            ),
            site="ticket_reviewer",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Reviewer produced no structured output."
            raise soft_failure(
                msg,
                raise_site="ticket_reviewer",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

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
            "approved": (
                TicketApproval.APPROVED
                if output.approved
                else TicketApproval.UNAPPROVED
            ),
            "review_feedback": output.feedback,
            "review_suggestions": output.suggestions,
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
                mode=self._review_mode,
            ),
        )
        return {}

    def _should_continue(
        self,
        state: TicketGenerationState,
    ) -> str:
        approved = state["approved"] is TicketApproval.APPROVED
        if approved or state["review_count"] >= self._max_reviews:
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
        graph.add_node("finalize", self._finalize_node)
        graph.add_edge(START, "create")
        if self._review_mode is TicketReviewMode.CREATE_ONLY:
            graph.add_edge("create", "finalize")
            graph.add_edge("finalize", END)
            return graph
        graph.add_node("review", self._review_node, retry_policy=self._retry)
        graph.add_edge("create", "review")
        graph.add_conditional_edges(
            "review",
            self._should_continue,
            ["create", "finalize"],
        )
        graph.add_edge("finalize", END)
        return graph
