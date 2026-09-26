"""Accumulate structural facts from the events that actually observed them."""

from kodezart.types.domain.agent import (
    AgentEvent,
    AuthoredWorkflowCompleteEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowPREvent,
    WorkflowScopeBaseEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.run_records import FireRecordFacts


def observe_fire_facts(facts: FireRecordFacts, event: AgentEvent) -> FireRecordFacts:
    """Fold one event into the facts the events so far have observed.

    Each fact is set by the event that carries it and by nothing else.
    ``iterations`` is the highest iteration seen go by, unknown until one
    has, and the loop's own total once the run completed: a fire that
    iterated and then failed still records how far it got.
    """
    if isinstance(event, WorkflowVisibilityEvent) and event.repo_url is not None:
        return facts.model_copy(update={"repo_url": event.repo_url})
    if isinstance(event, WorkflowScopeBaseEvent):
        return facts.model_copy(update={"base_branch": event.base_branch})
    if isinstance(event, WorkflowPREvent):
        return facts.model_copy(update={"pr_url": event.pr_url})
    if isinstance(event, WorkflowIterationEvent):
        highest = (
            event.iteration
            if facts.iterations is None
            else max(facts.iterations, event.iteration)
        )
        return facts.model_copy(update={"iterations": highest})
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
