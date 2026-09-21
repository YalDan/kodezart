"""Publish authored pull requests and bounded failure comments."""

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
)
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    AgentRunner,
    ArtifactPersister,
    OutboundContentGate,
    PRCreator,
    PromptSetProvider,
    RefPublisher,
)
from kodezart.core.stream_drain import drain
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
    validated_criteria,
)
from kodezart.types.domain.agent import (
    PR_DESCRIPTION_SCHEMA,
    PRDescriptionOutput,
    WorkflowPREvent,
)
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.workflow import (
    AuthoredWorkflowState,
    ExecutionContext,
)


class AuthoredPublication:
    """Publish authored pull requests and bounded failure comments."""

    def __init__(
        self,
        *,
        service: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        gate: OutboundContentGate,
        pr_creator: PRCreator | None,
        artifact_persister: ArtifactPersister | None,
        ref_publisher: RefPublisher | None,
        remediation_max_rounds: int,
    ) -> None:
        self._service = service
        self._prompts = prompts
        self._skills = skills
        self._gate = gate
        self._pr_creator = pr_creator
        self._artifact_persister = artifact_persister
        self._ref_publisher = ref_publisher
        self._remediation_max_rounds = remediation_max_rounds
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    @property
    def available(self) -> bool:
        """Whether this origin has a PR writer."""
        return self._pr_creator is not None

    async def open_stalled_pr(
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
            title=await gated_write(
                gate=self._gate,
                log=self._log,
                content=stall_pr_title(ticket.title),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_TITLE,
                content_class=ContentClass.AUTHORED,
            ),
            body=require_tracker_issue(
                await gated_write(
                    gate=self._gate,
                    log=self._log,
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

    async def open_pr(
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
            title=await gated_write(
                gate=self._gate,
                log=self._log,
                content=pr_output.title,
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PR_TITLE,
                content_class=ContentClass.AUTHORED,
            ),
            body=require_tracker_issue(
                await gated_write(
                    gate=self._gate,
                    log=self._log,
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

    async def comment_failure(
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
        comment_body = await gated_write(
            gate=self._gate,
            log=self._log,
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
