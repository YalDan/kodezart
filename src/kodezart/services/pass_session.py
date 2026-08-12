"""A scheduled pass, as the design states it: one prompt, sent as one session.

The cron fires, the prompt renders from the operation configuration, and
the rendered text goes to the existing agent query path as one session
carrying its grant.  THE SESSION DOES THE WORK: nothing here reads the
tracker, writes a report, or re-implements a clause of the prompt.  This
is the ``run`` callable a :class:`ScheduledPass` needs and its body is the
two acts the design names.

Rendering happens per tick rather than once at construction, so a pass
whose configuration stopped resolving fails on the tick that found it
instead of taking boot down for every other pass as well.  The failure
propagates: the scheduler records it and the next tick tries again, and no
session is ever started on a prompt with a hole in it.
"""

from collections.abc import Callable

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection


class PassSession:
    """One pass's rendered prompt, run as one agent session per tick."""

    def __init__(
        self,
        *,
        name: str,
        compose: Callable[[], str],
        runner: AgentRunner,
        workspace_path: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection,
        session_type: SessionType,
    ) -> None:
        self._name: str = name
        self._compose: Callable[[], str] = compose
        self._runner: AgentRunner = runner
        self._workspace_path: str = workspace_path
        self._permission_mode: str = permission_mode
        self._allowed_tools: list[str] = allowed_tools
        self._skills: SkillsSelection = skills
        self._session_type: SessionType = session_type
        self._log: BoundLogger = get_logger(__name__)

    @property
    def name(self) -> str:
        """What this pass is called wherever it is registered or reported."""
        return self._name

    async def run(self) -> None:
        """Render this pass's prompt and run it as one session."""
        prompt = self._compose()
        async for _event in self._runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=self._workspace_path,
            permission_mode=self._permission_mode,
            allowed_tools=self._allowed_tools,
            skills=self._skills,
            session_type=self._session_type,
        ):
            continue
        await self._log.ainfo("pass_session_finished", name=self._name)
