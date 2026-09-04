"""The shared remediation component — one round, one targeted ticket.

Every failure route in the pipeline reaches this one class.  It produces
a remediation TICKET and stops there: the fresh criteria the round needs
come from the criteria pipeline that already exists, reached by an edge
in the workflow graph rather than reimplemented here.  That is what keeps
the validation gate un-bypassable — there is no second criteria path to
remember to route through, because there is no second path.
"""

from collections.abc import AsyncIterator

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS_WITH_AGENT
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptProvider
from kodezart.core.stream_drain import drain
from kodezart.domain.remediation import done_work_summary
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import (
    TICKET_DRAFT_SCHEMA,
    AgentEvent,
    TicketDraftOutput,
    WorkflowRemediationEvent,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.workflow import RemediationRequest


class RemediationChain:
    """Turns failure evidence into a targeted follow-up ticket.

    Implements the ``Remediator`` protocol.  A single session with no
    ``session_id``: the round is a fresh conversation holding the
    original ticket, the done-work summary and the failure evidence, and
    nothing carried over from the run that failed — the point is to
    re-derive what must change from the evidence, not to continue the
    conversation that produced the failure.
    """

    def __init__(
        self,
        service: AgentRunner,
        *,
        prompts: PromptProvider,
        skills: SkillsSelection,
    ) -> None:
        self._service: AgentRunner = service
        self._prompts: PromptProvider = prompts
        self._skills: SkillsSelection = skills
        self._log: BoundLogger = get_logger(__name__)

    async def run(
        self,
        request: RemediationRequest,
        *,
        repo_path: str | None,
        repo_url: str | None,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Draft one remediation ticket for *request*."""
        prompt = self._prompts.template_for(PromptKey.REMEDIATION_TICKET).render(
            {
                "original_ticket": format_ticket_as_task(request.original_ticket),
                "done_work": done_work_summary(request),
                "failure_evidence": request.failure_evidence,
            },
        )

        result_event, rate_limit_rejected = await drain(
            self._service.stream(
                prompt=prompt,
                repo_path=repo_path,
                repo_url=repo_url,
                branch=request.work_base_ref,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=EVAL_TOOLS_WITH_AGENT,
                skills=self._skills,
                output_format={
                    "type": "json_schema",
                    "schema": TICKET_DRAFT_SCHEMA,
                },
                cache_key=cache_key,
            ),
            site="remediation_ticket",
        )

        if result_event is None or result_event.structured_output is None:
            msg = "Agent did not produce structured output for remediation ticket"
            raise soft_failure(
                msg,
                raise_site="remediation_ticket",
                result_event=result_event,
                rate_limit_rejected=rate_limit_rejected,
            )

        ticket = TicketDraftOutput.model_validate(result_event.structured_output)
        await self._log.ainfo(
            "remediation_ticket_drafted",
            entry=request.entry.value,
            round_index=request.round_index,
            base_ref=request.work_base_ref,
            title=ticket.title,
        )
        yield WorkflowRemediationEvent(
            entry=request.entry,
            round_index=request.round_index,
            ticket=ticket,
            base_ref=request.work_base_ref,
        )
