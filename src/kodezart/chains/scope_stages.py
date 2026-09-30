"""The scope run's own nodes: groom and prep the parent, then count what is open."""

from langchain_core.runnables import RunnableConfig

from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.protocols import AgentRunner, PromptSetProvider, ScopeMemberPager
from kodezart.domain.criteria import criterion_set
from kodezart.domain.fire_spec import criterion_check, tracker_spec_from_board
from kodezart.domain.prompt_variables import scope_variables
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.tracker import is_open
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState

#: The open keys a failed scope-done gate names in its feedback, at most.
_FEEDBACK_KEYS = 10


class ScopeStages:
    """Groom, prep and the done question over the parent a scope run names.

    Groom and prep are one session each over the organize prompt, its ticket
    phase and then its criteria phase: the session works the board with the
    tracker tools its kind is given, and what it writes there is the whole of
    what happens. Prep then reads the criterion sub-issues below the parent
    through the tracker port, page by page, and their keys with their Checks
    are what the loop and the review grade. After the merge the scope-done
    gate counts, through the same port, how many issues below the parent are
    still open: arithmetic over the board's states, no session and no list
    handed back through a structured answer, which at a thousand members no
    longer fit one (KOD-1288). Nothing here writes to the tracker itself:
    the sessions do.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        members: ScopeMemberPager,
        working_dir: str,
    ) -> None:
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._members = members
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
        checks = await self._checks(scope)
        if not checks:
            return {"criteria_infeasible": True}
        roster = criterion_set(checks)
        return {
            "fire_spec": tracker_spec_from_board(
                subject_key=scope.key,
                body=ctx.prompt,
                criterion_keys=[c.id for c in roster.criteria],
            ),
            "criterion_set": roster,
        }

    async def scope_done(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """Passed when nothing below the parent is open; else the count, a few keys.

        Every page is read: an open issue on the last page is as open as one
        on the first. A parent with nothing below it is not finished, it is
        unread work.
        """
        _ = state
        open_keys: list[str] = []
        total = 0
        async for page in self._members.scope_member_pages(ref=_scope_of(config)):
            for issue in page:
                total += 1
                if is_open(issue.state_kind):
                    open_keys.append(issue.issue_key)
        if total == 0:
            return {
                "review_passed": False,
                "review_feedback": "Nothing was read below the parent.",
            }
        if not open_keys:
            return {"review_passed": True}
        named = ", ".join(open_keys[:_FEEDBACK_KEYS])
        return {
            "review_passed": False,
            "review_feedback": (
                f"Open below the parent: {len(open_keys)} of {total}, "
                f"among them {named}."
            ),
        }

    async def _checks(self, scope: ScopeRef) -> dict[str, str]:
        """Each criterion sub-issue below *scope* with its Check, in board order.

        Read through the tracker port one listing page at a time, each body
        whole: a listing may cut a Check short, and a cut Check is not the
        board's. A key the pages repeat is held once, where it first stood,
        with the Check it was read with last.
        """
        checks: dict[str, str] = {}
        async for page in self._members.scope_member_pages(
            ref=scope, whole_bodies=True
        ):
            for issue in page:
                if "criterion" in issue.issue_labels:
                    checks[issue.issue_key] = criterion_check(
                        criterion=issue, issue_key=scope.key
                    )
        return checks

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
