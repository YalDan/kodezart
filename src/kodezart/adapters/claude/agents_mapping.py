"""Session-policy and subagent-definition mapping onto the SDK options.

One module builds every SDK shape the widened seam carries, so the domain
models cross the port and the SDK's dataclasses never do.  Absence maps to
absence: a dispatch that declares nothing produces exactly the options the
seam constructed before it widened.
"""

import json
from collections.abc import Sequence
from typing import Final, Literal

from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition
from claude_agent_sdk.types import SdkPluginConfig, SystemPromptPreset

from kodezart.types.domain.session import AllowedTools, ToolPreset
from kodezart.types.domain.subagents import (
    AgentDefinition,
    SessionEffort,
    SessionPolicy,
    WorkflowAccess,
)

#: The preset the append rides. The house rules are an ADDITION to the
#: harness's own system prompt, never a replacement for it.
_SYSTEM_PROMPT_PRESET: Final[Literal["claude_code"]] = "claude_code"

#: Environment names the harness reads for workflow availability. Both are
#: on/off switches: the harness reads ``CLAUDE_CODE_WORKFLOWS`` as a boolean
#: and never as a directory, so the workflows reach a session as a plugin.
_WORKFLOWS_VAR: Final = "CLAUDE_CODE_WORKFLOWS"
_WORKFLOWS_DISABLED_VAR: Final = "CLAUDE_CODE_DISABLE_WORKFLOWS"
_SWITCH_ON: Final = "1"

#: The settings key bounding how wide one workflow may fan out. Its value is
#: a named size, never a count: each size aims below its bound, the last sets
#: none, and a value outside these makes the harness discard the whole
#: settings object, output style included.
_SIZE_GUIDELINE_KEY: Final = "workflowSizeGuideline"
_SIZE_GUIDELINES: Final = (("small", 5), ("medium", 10), ("large", 50))
_UNRESTRICTED_SIZE: Final = "unrestricted"

#: The settings key naming the output style a session's system prompt runs
#: under.  The Python SDK offers no programmatic option for it, so the
#: settings object the CLI already reads is where it is stated.
_OUTPUT_STYLE_KEY: Final = "outputStyle"


def map_agents(
    definitions: Sequence[AgentDefinition],
) -> dict[str, SDKAgentDefinition] | None:
    """Map typed lens definitions onto the SDK ``agents`` option.

    An empty sequence maps to ``None`` — the option the SDK reads as "this
    session defines no agents" — so the guarantee survives the mapping
    rather than becoming an empty dictionary the CLI would still carry.
    """
    if not definitions:
        return None
    return {
        definition.name: SDKAgentDefinition(
            description=definition.description,
            prompt=definition.prompt,
            tools=list(definition.tools),
        )
        for definition in definitions
    }


def map_system_prompt(policy: SessionPolicy) -> SystemPromptPreset | None:
    """Map the declared append onto the SDK ``system_prompt`` option."""
    if policy.system_prompt_append is None:
        return None
    return SystemPromptPreset(
        type="preset",
        preset=_SYSTEM_PROMPT_PRESET,
        append=policy.system_prompt_append,
    )


def map_effort(
    effort: SessionEffort | None,
) -> Literal["low", "medium", "high", "xhigh", "max"] | None:
    """Map the declared effort level onto the SDK ``effort`` option."""
    if effort is None:
        return None
    return _SDK_EFFORT[effort]


_SDK_EFFORT: Final[
    dict[SessionEffort, Literal["low", "medium", "high", "xhigh", "max"]]
] = {
    SessionEffort.LOW: "low",
    SessionEffort.MEDIUM: "medium",
    SessionEffort.HIGH: "high",
    SessionEffort.XHIGH: "xhigh",
    SessionEffort.MAX: "max",
}


def map_workflow_env(access: WorkflowAccess | None) -> dict[str, str]:
    """Map the workflow gates onto the SDK ``env`` option.

    A session that declares no access contributes no variables at all,
    which is what leaves an undeclaring dispatch byte-identical to today's.
    """
    if access is None:
        return {}
    if not access.enabled:
        return {_WORKFLOWS_DISABLED_VAR: _SWITCH_ON}
    return {_WORKFLOWS_VAR: _SWITCH_ON}


def map_plugins(access: WorkflowAccess | None) -> list[SdkPluginConfig]:
    """Map the workflows' plugin onto the SDK ``plugins`` option.

    The harness finds a named workflow under the session's working
    directory or in a loaded plugin; a scheduled pass runs in a scratch
    directory and a fire in a clone of another repository, so the plugin
    is what makes the shipped workflows reachable from both.
    """
    if access is None or not access.enabled:
        return []
    return [SdkPluginConfig(type="local", path=access.plugin_path)]


def map_settings(access: WorkflowAccess | None, output_style: str | None) -> str | None:
    """Map every Claude Code setting one dispatch declares onto ``settings``.

    The SDK accepts either a settings file path or an inline JSON object
    for this ONE option and forwards it to the CLI unchanged, so the
    fan-out bound and the output style are two keys of one object rather
    than two options.  A dispatch declaring neither passes no settings at
    all, which leaves the CLI's own settings sources — and with them its
    own default style — exactly as they were.
    """
    settings: dict[str, object] = {}
    if access is not None and access.enabled:
        settings[_SIZE_GUIDELINE_KEY] = _size_guideline(access.size_guideline)
    if output_style is not None:
        settings[_OUTPUT_STYLE_KEY] = output_style
    if not settings:
        return None
    return json.dumps(settings)


def _size_guideline(cap: int) -> str:
    """The smallest named size whose bound *cap* stays below."""
    return next(
        (name for name, bound in _SIZE_GUIDELINES if cap < bound),
        _UNRESTRICTED_SIZE,
    )


def map_model(policy: SessionPolicy, construction_model: str | None) -> str | None:
    """Resolve the engine for one dispatch.

    A per-call model overrides the model the executor was constructed
    with; declaring none leaves the construction-time value in force.
    """
    if policy.model is not None:
        return policy.model
    return construction_model


_SDK_TOOL_PRESETS: Final[dict[ToolPreset, tuple[str, ...]]] = {
    ToolPreset.EVALUATION: ("Read", "Glob", "Grep", "Bash", "Workflow"),
    ToolPreset.DELEGATED_EVALUATION: (
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Agent",
        "Workflow",
    ),
    ToolPreset.AUTHORING: (
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Agent",
        "WebSearch",
        "WebFetch",
        "Workflow",
    ),
    ToolPreset.IMPLEMENTATION: (
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Edit",
        "Write",
        "Workflow",
    ),
}


def map_allowed_tools(selection: AllowedTools) -> list[str]:
    """Expand an application bundle or retain the caller's open selectors."""
    if isinstance(selection, list):
        return selection
    return list(_SDK_TOOL_PRESETS[selection])
