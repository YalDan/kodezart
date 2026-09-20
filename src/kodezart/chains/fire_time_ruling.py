"""The pre-loop question step, as the native fire graph runs it.

The module holds no logic of its own: it reads the captured subject, the
current Checks and the run's context off the state the re-validation step
left, hands them to the service, and routes on. Nothing the pass produces
enters graph state — the record is on the tracker, and the loop's own writer
contract reads it from there.
"""

from langchain_core.runnables import RunnableConfig

from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState


async def rule_open_questions(
    state: WorkflowState,
    config: RunnableConfig,
    *,
    rulings: FireTimeRulings,
) -> dict[str, object]:
    """Answer this fire's open questions before its first iteration."""
    spec = state["fire_spec"]
    if not isinstance(spec, TrackerSpec):
        raise ValueError("The open-question step runs only on a tracker subject")
    criteria = state["criterion_set"]
    if not isinstance(criteria, TrackerCriterionSet):
        raise ValueError("The open-question step needs the current tracker Checks")
    ctx = ExecutionContext.from_configurable(config)
    await rulings.rule(
        spec=spec,
        criteria=criteria,
        repo_path=ctx.repo_path,
        repo_url=ctx.repo_url,
        ref=state["work_base_ref"],
        cache_key=ctx.cache_key,
        holder=ctx.surface_holder,
        visibility=state["repo_visibility"],
    )
    return {}


def route_after_questions(state: WorkflowState) -> str:
    """Enter the loop, or stop here when no answer was confirmed."""
    _ = state
    return "run_ralph_loop"
