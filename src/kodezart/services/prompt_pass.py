"""A scheduled pass, as the design states it: one prompt, sent as one session.

The cron fires, the prompt renders from the operation configuration, and
the rendered text goes to the existing agent query path as one session
carrying its grant.  **THE SESSION DOES THE WORK**: nothing here reads the
tracker, writes a report, or re-implements a clause of the prompt.

One function for every prompt pass rather than one per pass.  The passes
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

from kodezart.core.error_egress import redact_credentials
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.services.pass_gate import PassGate
from kodezart.types.domain.agent import ErrorEvent, ResultEvent
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection

_log: BoundLogger = get_logger(__name__)


async def run_prompt_pass(
    *,
    key: PromptKey,
    prompts: PromptSetProvider,
    runner: AgentRunner,
    gate: PassGate | None,
    workspace_path: str,
    permission_mode: str,
    allowed_tools: list[str],
    skills: SkillsSelection,
    session_type: SessionType,
) -> None:
    """Render *key*'s prompt and run it as one session, unless its gate is quiet.

    An absent *gate* means this pass is ungated and issues no query at all
    — the cheapest path, and a deliberate configuration rather than a
    degraded one.  A gate that reports nothing skips the session entirely,
    which is the whole token saving.

    Raises :class:`PromptRenderError` naming every unconditional
    placeholder without a config value — a pass whose identities cannot
    all be resolved refuses to run rather than running on a hole.  The
    render is attempted only after the gate has said there is work, so a
    quiet board never pays for a render either.

    *skills* is the DEPLOYMENT's selection and is narrowed by the set
    before it reaches the session, the same consumer shape every other
    dispatch site uses: the deployment decides what is available and the
    set decides what a role reaches for.  The effort the set declares
    for *key* travels with it, so a pass runs at the level its own set
    states rather than at whatever the harness happens to default to.

    Ends on one of two events, and neither of them claims more than it
    observed.  ``prompt_pass_failed`` means an ``ErrorEvent`` came down
    the stream; ``prompt_pass_finished`` means one did not, which is what
    ``result_event_observed`` and the per-type counts are for — a session
    that opened no tool at all reports ``tool_use`` nowhere in its counts,
    and is thereby distinguishable from one that made a dozen calls.

    Cancellation — the scheduler abandoning a tick that outran its budget
    — is not an outcome this reports.  There is no handler around the
    read, so ``CancelledError`` unwinds through it and no terminal event
    is emitted for a pass that never terminated.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    if gate is not None and not (await gate.delta()).has_delta():
        await _log.ainfo("prompt_pass_skipped_no_delta", name=key.value)
        return
    prompt = prompts.template_for(key).render({})
    counts: Counter[str] = Counter()
    failure: ErrorEvent | None = None
    result_observed = False
    async for event in runner.stream_in_workspace(
        prompt=prompt,
        workspace_path=workspace_path,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        skills=prompts.session_skills(key, skills),
        session_type=session_type,
        session_policy=prompts.session_policy(key),
    ):
        counts[event.type] += 1
        if isinstance(event, ErrorEvent):
            if failure is None:
                failure = event
        elif isinstance(event, ResultEvent):
            result_observed = True
    observed: dict[str, object] = {
        "name": key.value,
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
        return
    await _log.ainfo("prompt_pass_finished", **observed)
