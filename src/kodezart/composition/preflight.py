"""Boot-time validation of the skills surface.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.core.errors import SkillPreflightError
from kodezart.core.protocols import (
    PromptProvider,
    SkillInventory,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsMode, SkillsSelection


def preflight_skills(
    selection: SkillsSelection,
    inventory: SkillInventory,
) -> None:
    """Fail loudly at boot when a configured skill name is not provisioned.

    Only EXPLICIT mode names skills.  Under NONE and ALL there is nothing to
    resolve, so nothing is checked.  The SDK forwards unknown names verbatim
    and silently filters them, so this is the only place the gap can surface.
    """
    if selection.mode is not SkillsMode.EXPLICIT:
        return
    available = inventory.available()
    unresolvable = [name for name in selection.allowlist if name not in available]
    if unresolvable:
        msg = "Configured skills are not provisioned on this host"
        raise SkillPreflightError(
            msg,
            unresolvable=unresolvable,
            available=sorted(available),
        )


def preflight_prompt_skill_loadouts(
    selection: SkillsSelection,
    prompts: PromptProvider,
) -> None:
    """Every per-key skills loadout must be a subset of the registered set.

    Only meaningful under EXPLICIT, where the allowlist IS the registration
    set.  Under NONE and ALL nothing is registered by name, so there is no
    subset relation to check.
    """
    if selection.mode is not SkillsMode.EXPLICIT:
        return
    registered = set(selection.allowlist)
    unresolvable = sorted(
        {
            name
            for key in PromptKey
            for name in prompts.declared_skills(key)
            if name not in registered
        }
    )
    if unresolvable:
        msg = "Prompt-set skill loadouts name skills that are not registered"
        raise SkillPreflightError(
            msg,
            unresolvable=unresolvable,
            available=sorted(registered),
        )
