"""Accumulate structural facts from the events that actually observed them."""

from kodezart.types.domain.agent import (
    AgentEvent,
    AuthoredWorkflowCompleteEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
    WorkflowScopeBaseEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.run_records import FireRecordFacts


def observe_fire_facts(facts: FireRecordFacts, event: AgentEvent) -> FireRecordFacts:
    if isinstance(event, WorkflowVisibilityEvent) and event.repo_url is not None:
        return facts.model_copy(update={"repo_url": event.repo_url})
    if isinstance(event, WorkflowScopeBaseEvent):
        return facts.model_copy(update={"base_branch": event.base_branch})
    if isinstance(event, WorkflowPREvent):
        return facts.model_copy(update={"pr_url": event.pr_url})
    if isinstance(event, WorkflowCompleteEvent):
        return FireRecordFacts(
            repo_url=facts.repo_url,
            base_branch=facts.base_branch,
            pr_url=(
                event.pr_url
                if isinstance(event, AuthoredWorkflowCompleteEvent)
                and event.pr_url is not None
                else facts.pr_url
            ),
            iterations=event.total_iterations,
        )
    return facts
