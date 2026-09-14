"""Permission mode validation for Claude Agent SDK."""

from typing import Literal

PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

_PERMISSION_MODE_MAP: dict[str, PermissionMode] = {
    "default": "default",
    "acceptEdits": "acceptEdits",
    "plan": "plan",
    "bypassPermissions": "bypassPermissions",
}


def _validate_permission_mode(mode: str) -> PermissionMode:
    resolved = _PERMISSION_MODE_MAP.get(mode)
    if resolved is None:
        msg = f"Invalid permission mode: {mode}"
        raise ValueError(msg)
    return resolved
