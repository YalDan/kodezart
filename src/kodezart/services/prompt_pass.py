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
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.services.pass_gate import PassGate
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
    """
    if gate is not None and not (await gate.delta()).has_delta():
        await _log.ainfo("prompt_pass_skipped_no_delta", name=key.value)
        return
    prompt = prompts.template_for(key).render({})
    async for _event in runner.stream_in_workspace(
        prompt=prompt,
        workspace_path=workspace_path,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        skills=prompts.session_skills(key, skills),
        session_type=session_type,
        session_policy=prompts.session_policy(key),
    ):
        continue
    await _log.ainfo("prompt_pass_finished", name=key.value)
