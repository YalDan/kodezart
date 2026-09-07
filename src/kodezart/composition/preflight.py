"""Boot-time validation of the skills surface.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.host_skill_inventory import HostSkillInventory
from kodezart.core.config import AppConfig
from kodezart.core.errors import SkillPreflightError
from kodezart.core.logging import BoundLogger
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


async def boot_skills(
    *,
    config: AppConfig,
    prompts: PromptProvider,
    log: BoundLogger,
) -> SkillsSelection:
    """Resolve the skills surface and hold it against the host and the sets.

    Both preflights run before anything is served, because both failures
    are silent at use time: the SDK filters an unprovisioned skill without
    saying so, and a loadout naming an unregistered skill renders a prompt
    that quietly loads nothing.
    """
    skills = config.skills_selection()
    preflight_skills(skills, HostSkillInventory(home_dir=config.claude_home_dir))
    preflight_prompt_skill_loadouts(skills, prompts)
    await log.ainfo(
        "skills_selection_resolved",
        mode=skills.mode.value,
        allowlist=list(skills.allowlist),
        setting_sources=config.setting_sources,
    )
    return skills
