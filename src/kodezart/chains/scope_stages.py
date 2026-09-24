"""The scope run's own nodes: groom and prep the parent, then ask if it is done."""

from langchain_core.runnables import RunnableConfig

from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.domain.prompt_variables import scope_variables
from kodezart.services.agent_question import ask
from kodezart.types.domain.agent import ScopeItemsOutput
from kodezart.types.domain.criteria import (
    CriterionId,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.fire_spec import IssueRef, TrackerSpec
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState


class ScopeStages:
    """Groom, prep and the done question over the parent a scope run names.

    Groom and prep are one session each over the organize prompt, its ticket
    phase and then its criteria phase: the session works the board with the
    tracker tools its kind is given, and what it writes there is the whole of
    what happens. Prep then asks the scope-done question, and the criterion
    keys it lists with their Checks are what the loop and the review grade.
    After the merge the same question says whether every issue below the
    parent is completed or canceled. Nothing here writes to the tracker
    itself: the sessions do.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        working_dir: str,
    ) -> None:
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._working_dir = working_dir

    async def groom(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """One session over the parent, the organize prompt's ticket phase."""
        _ = state
        await self._session(_scope_of(config), phase="phase_ticket")
        return {}

    async def prep(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """The criteria phase, then the run's spec from the board's criteria."""
        _ = state
        ctx = ExecutionContext.from_configurable(config)
        scope = _scope_of(config)
        await self._session(scope, phase="phase_criteria")
        criteria = [
            TrackerCriterion(id=CriterionId(item.key), text=item.text)
            for item in (await self._items(scope)).items
            if item.criterion
        ]
        if not criteria:
            return {"criteria_infeasible": True}
        return {
            "fire_spec": TrackerSpec(
                subject=IssueRef(scope.key),
                body=ctx.prompt,
                criteria=tuple(CriterionRef(c.id) for c in criteria),
                read_at_version="board",
            ),
            "criterion_set": TrackerCriterionSet(criteria=criteria),
        }

    async def scope_done(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """Whether every issue below the parent is completed or canceled."""
        _ = state
        answer = await self._items(_scope_of(config))
        if answer.items and all(item.done for item in answer.items):
            return {"review_passed": True}
        still_open = ", ".join(item.key for item in answer.items if not item.done)
        return {
            "review_passed": False,
            "review_feedback": (
                f"Open below the parent: {still_open or 'none'}. {answer.reason}"
            ),
        }

    async def _items(self, scope: ScopeRef) -> ScopeItemsOutput:
        """Every issue below *scope* as the board shows it; none when unanswered."""
        answer = await ask(
            runner=self._runner,
            prompts=self._prompts,
            skills=self._skills,
            workspace_path=self._working_dir,
            key=PromptKey.SCOPE_DONE,
            bindings=scope_variables(scope),
            answer=ScopeItemsOutput,
        )
        if answer is None:
            return ScopeItemsOutput(
                items=[], reason="The scope-done question went unanswered."
            )
        return answer

    async def _session(self, scope: ScopeRef, *, phase: str) -> None:
        """One unattended board session over *scope* for one organize phase."""
        prompt = self._prompts.template_for(PromptKey.ORGANIZE_SESSION).render(
            {**scope_variables(scope), phase: True}
        )
        async for _event in self._runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=self._working_dir,
            permission_mode=UNATTENDED_PERMISSION_MODE,
            allowed_tools=[],
            skills=self._prompts.session_skills(
                PromptKey.ORGANIZE_SESSION, self._skills
            ),
            session_type=SessionType.ORGANIZE_PASS,
            agents=NO_SUBAGENTS,
            session_policy=self._prompts.session_policy(PromptKey.ORGANIZE_SESSION),
        ):
            pass


def _scope_of(config: RunnableConfig) -> ScopeRef:
    """The parent this run is addressed at; a scope node needs one."""
    scope = ExecutionContext.from_configurable(config).scope
    if scope is None:
        raise ValueError("A scope node runs only on a run addressed at a scope")
    return scope
