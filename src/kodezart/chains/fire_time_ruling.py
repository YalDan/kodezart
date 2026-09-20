"""The pre-loop question step, as the native fire graph runs it.

The module holds no logic of its own: it reads the captured subject, the
current Checks and the run's context off the state the re-validation step
left, hands them to the service, and routes on. Nothing the pass produces
enters graph state — the record is on the tracker, and the loop's own writer
contract reads it from there.
"""

from langchain_core.runnables import RunnableConfig

from kodezart.core.logging import get_logger
from kodezart.domain.errors import RulingUnrecordedError
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.types.domain.criteria import TrackerCriterionSet
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState

_log = get_logger(__name__)


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
    try:
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
    except RulingUnrecordedError as refusal:
        # Only this one failure ends the fire here. A pass that produced
        # nothing, or a transport failure before the first write, is the
        # graph's retry and never a terminal about the tracker.
        await _log.ainfo(
            "fire_open_question_unrecorded",
            issue_key=refusal.issue_key,
            reason=refusal.reason,
        )
        return {"ruling_unrecorded": True}
    return {}


def route_after_questions(state: WorkflowState) -> str:
    """Enter the loop, or stop here when no answer was confirmed."""
    if state.get("ruling_unrecorded", False):
        return "complete"
    return "run_ralph_loop"
