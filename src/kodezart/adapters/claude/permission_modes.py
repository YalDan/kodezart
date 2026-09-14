"""Translate application permission choices at the Claude SDK boundary."""

from claude_agent_sdk.types import PermissionMode as SDKPermissionMode

from kodezart.types.domain.session import PermissionMode

_PERMISSION_MODE_MAP: dict[PermissionMode, SDKPermissionMode] = {
    PermissionMode.INTERACTIVE: "default",
    PermissionMode.ACCEPT_EDITS: "acceptEdits",
    PermissionMode.PLAN: "plan",
    PermissionMode.UNATTENDED: "bypassPermissions",
}


def map_permission_mode(mode: PermissionMode) -> SDKPermissionMode:
    """Reject untyped inputs before constructing or opening an SDK session."""
    if not isinstance(mode, PermissionMode):
        raise ValueError(f"Invalid permission mode: {mode}")
    return _PERMISSION_MODE_MAP[mode]
