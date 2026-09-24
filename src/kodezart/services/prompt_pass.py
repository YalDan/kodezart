"""A scheduled pass, as the design states it: one prompt, sent as one session.

The cron fires, the prompt renders from the operation configuration, and
the rendered text goes to the existing agent query path as one session
carrying its grant.  **THE SESSION DOES THE WORK**: nothing here reads the
tracker, writes a report, or re-implements a clause of the prompt.

Before the session, the gate: one short session of the same kind, asked
whether anything moved in the pass's window that the pass should act on,
answered in a structured shape (``PassGateOutput``).  The window starts at
the last tick of this pass that ran, in this process; the first tick after
boot has no window and runs without asking, which is what ``tick_at_boot``
means.  A ``run: false`` answer skips the session and the pass sleeps its
interval; ``run: true`` opens it; an answer that is missing or that cannot
be read runs the pass, named in its own event, because a broken answer is
never a reason to leave the board unread.  The gate is the same shape as
every other structured session here — a prompt key, a template, an output
model whose schema is the wire contract, asked through the one shared
question (``services/agent_question.py``) — and its engine is whatever
``session_models`` pins the ``pass_gate`` key to.

One class for every prompt pass rather than one per pass.  The passes
differ in exactly one value — which template to render — so a second copy
of this body would be a second path that can drift out of parity with the
first, which is the defect this module exists to remove.

Rendering happens per tick rather than once at registration, so a pass
whose configuration stopped resolving fails on the tick that found it
instead of taking boot down for every other pass as well.  The failure
propagates: the scheduler records it, the next tick tries again, and no
session is ever started on a prompt with a hole in it.

The session's stream is READ rather than discarded.  A pass that reached
the tracker and a pass that produced nothing at all end the same way —
the stream runs out — so a completion event naming only the pass says
nothing about whether the pass did anything, and an error event that
arrived mid-stream used to be consumed and dropped on the way past.  What
the stream carried therefore rides both terminal events: the event counts,
whether a terminal result arrived, and how long the pass took.
"""

import asyncio
from collections import Counter
from datetime import datetime

from kodezart.core.error_egress import redact_credentials
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.services.agent_question import ask
from kodezart.types.domain.agent import (
    ErrorEvent,
    PassGateOutput,
    ResultEvent,
)
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import AllowedTools, PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection

_log: BoundLogger = get_logger(__name__)


def pass_render_bindings(identity: RunIdentity) -> dict[str, object]:
    """The per-call namespace every rendering of a pass prompt binds.

    One function because there are two renderers of these templates — the
    tick, and the boot preflight that proves they resolve — and a template
    the preflight rendered with a name the tick does not bind would pass
    boot and refuse on the first interval.
    """
    return {"record_title": identity.title()}


def gate_render_bindings(*, name: str, window_start: datetime) -> dict[str, object]:
    """The per-tick namespace the gate question binds, last in its template.

    The pass's name and the window's start are the only values that change
    from one tick to the next; everything before them in the template is
    stable across ticks, so the prompt prefix caches.  Shared with the boot
    preflight for the same reason as :func:`pass_render_bindings`.
    """
    return {"pass_name": name, "window_start": window_start.isoformat()}


class PromptPass:
    """One scheduled prompt pass: its gate question, then its session."""

    def __init__(
        self,
        *,
        kind: RunKind,
        key: PromptKey,
        prompts: PromptSetProvider,
        runner: AgentRunner,
        workspace_path: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        skills: SkillsSelection,
        session_type: SessionType,
    ) -> None:
        self._kind: RunKind = kind
        self._key: PromptKey = key
        self._prompts: PromptSetProvider = prompts
        self._runner: AgentRunner = runner
        self._workspace_path: str = workspace_path
        self._permission_mode: PermissionMode = permission_mode
        self._allowed_tools: AllowedTools = allowed_tools
        #: The DEPLOYMENT's selection, narrowed by the set before it reaches
        #: a session: the deployment decides what is available and the set
        #: decides what a role reaches for.
        self._skills: SkillsSelection = skills
        self._session_type: SessionType = session_type
        #: When the last tick that ran began: the next window's start.  None
        #: until a tick has run in this process, so the first tick asks
        #: nothing and runs.
        self._ran_at: datetime | None = None

    @property
    def window_start(self) -> datetime | None:
        """Where the next gate question's window starts, or none before a run."""
        return self._ran_at

    async def run(self, started_at: datetime) -> PassRun:
        """Ask the gate, then render the pass's prompt and run it as one session.

        Which of the two happened is RETURNED rather than only logged,
        because the caller has an obligation that turns on it: a skipped
        tick produced no run, and the record its scheduler would otherwise
        backfill would assert one.

        Raises :class:`PromptRenderError` naming every unconditional
        placeholder without a config value — a pass whose identities cannot
        all be resolved refuses to run rather than running on a hole.

        Ends on one of two events, and neither of them claims more than it
        observed.  ``prompt_pass_failed`` means an ``ErrorEvent`` came down
        the stream; ``prompt_pass_finished`` means one did not, which is
        what ``result_event_observed`` and the per-type counts are for — a
        session that opened no tool at all reports ``tool_use`` nowhere in
        its counts, and is thereby distinguishable from one that made a
        dozen calls.

        Cancellation — the scheduler abandoning a tick that outran its
        budget — is not an outcome this reports, and neither is a raise:
        no terminal event is emitted for a pass that never terminated, and
        the window is not advanced for one, so the next tick asks about
        the same window again.

        *started_at* and *kind* are this run's identity.  The row title
        they spell goes into the render, so the session's Record clause
        prescribes the EXACT string the runner will look for, and the same
        instant becomes the next window's start once the session has run.
        """
        if self._ran_at is not None:
            answer = await self._ask(window_start=self._ran_at)
            if answer is not None and not answer.run:
                return PassRun.SKIPPED
        loop = asyncio.get_running_loop()
        started = loop.time()
        counts: Counter[str] = Counter()
        failure: ErrorEvent | None = None
        result_observed = False
        identity = RunIdentity(
            kind=self._kind, name=self._key.value, started_at=started_at
        )
        prompt = self._prompts.template_for(self._key).render(
            pass_render_bindings(identity)
        )
        async for event in self._runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=self._workspace_path,
            permission_mode=self._permission_mode,
            allowed_tools=self._allowed_tools,
            skills=self._prompts.session_skills(self._key, self._skills),
            session_type=self._session_type,
            session_policy=self._prompts.session_policy(self._key),
        ):
            counts[event.type] += 1
            if isinstance(event, ErrorEvent):
                if failure is None:
                    failure = event
            elif isinstance(event, ResultEvent):
                result_observed = True
        self._ran_at = started_at
        observed: dict[str, object] = {
            "name": self._key.value,
            "duration_seconds": loop.time() - started,
            "events": dict(counts),
            "event_count": sum(counts.values()),
            "result_event_observed": result_observed,
        }
        if failure is not None:
            await _log.aerror(
                "prompt_pass_failed",
                error=redact_credentials(failure.error),
                error_kind=failure.error_kind,
                **observed,
            )
            return PassRun.RAN
        await _log.ainfo("prompt_pass_finished", **observed)
        return PassRun.RAN

    async def _ask(self, *, window_start: datetime) -> PassGateOutput | None:
        """The gate question over *window_start*, or ``None`` for no usable answer.

        Asked through :func:`ask` on the ``pass_gate`` key's own policy.  A
        missing or unreadable answer is ``None``, and the caller runs the pass
        on it.  A raise is not an answer and propagates to the scheduler.
        """
        name = self._key.value
        answer = await ask(
            runner=self._runner,
            prompts=self._prompts,
            skills=self._skills,
            workspace_path=self._workspace_path,
            key=PromptKey.PASS_GATE,
            bindings=gate_render_bindings(name=name, window_start=window_start),
            answer=PassGateOutput,
        )
        if answer is not None:
            await _log.ainfo(
                "pass_gate_answered",
                name=name,
                run=answer.run,
                moved_count=len(answer.moved),
                reason=answer.reason,
            )
        return answer
