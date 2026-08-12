"""The grooming pass's whole code half: its prompt, rendered.

The pass is a judgment surface running as a full agent session with the
tracker attached — the session does the work.  What the code owns is one
render, the mirror of :meth:`FirePrepPass.compose_prompt`, which is that
same act for the other pass.

A function rather than a service because grooming carries none of the
deterministic gates fire-prep owns, and a class holding a single render
call would be a service in name only.
"""

from kodezart.core.protocols import PromptProvider
from kodezart.types.domain.prompts import PromptKey


def compose_grooming_prompt(prompts: PromptProvider) -> str:
    """The grooming prompt, rendered through the registry from configuration.

    Raises :class:`PromptRenderError` naming every unconditional placeholder
    without a config value — a pass whose identities cannot all be resolved
    refuses to exist rather than running on a hole.
    """
    return prompts.template_for(PromptKey.GROOMING_PASS).render({})
