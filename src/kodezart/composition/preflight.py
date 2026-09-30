"""Boot-time validation of the skills surface, and the host-MCP opt-in's warning.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.host_skill_inventory import HostSkillInventory
from kodezart.config.agent import AgentSettings
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
    settings: AgentSettings,
    prompts: PromptProvider,
    log: BoundLogger,
) -> SkillsSelection:
    """Resolve the skills surface and hold it against the host and the sets.

    Both preflights run before anything is served, because both failures
    are silent at use time: the SDK filters an unprovisioned skill without
    saying so, and a loadout naming an unregistered skill renders a prompt
    that quietly loads nothing.
    """
    skills = settings.skills
    preflight_skills(skills, HostSkillInventory(home_dir=settings.home_dir))
    preflight_prompt_skill_loadouts(skills, prompts)
    await log.ainfo(
        "skills_selection_resolved",
        mode=skills.mode.value,
        allowlist=list(skills.allowlist),
        setting_sources=settings.setting_sources,
    )
    return skills


#: The event boot logs, once, when the working-directory MCP guard is off.
HOST_MCP_ALLOWED_EVENT = "host_mcp_allowed_dangerously"


async def warn_host_mcp_opt_in(
    *,
    settings: AgentSettings,
    log: BoundLogger,
) -> None:
    """Say at boot, once and as a warning, that the MCP guard is off.

    Off is the shipped state and logs nothing: there is no risk to name.
    On means every session this process starts also loads the servers the
    operator's own Claude configuration declares and any server its working
    directory declares, so a cloned repository can start a command on this
    machine through a session, and a tracker write made through the host's
    stored login carries the operator's user rather than this deployment's
    key.  The startup log is where an operator reads what a boot decided,
    so that is where the decision is named.
    """
    if not settings.dangerously_allow_host_mcp:
        return
    await log.awarning(
        HOST_MCP_ALLOWED_EVENT,
        risk=(
            "strict_mcp_config is off for every session: a working "
            "directory's .mcp.json can start a command on this machine, and "
            "the host's user-level MCP servers run under the operator's "
            "stored login"
        ),
    )
