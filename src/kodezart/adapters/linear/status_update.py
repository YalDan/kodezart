"""``ScopeStatusWriter`` over the tracker's MCP server.

One class for one role over the caller the tracker already dials, following
the record sink rather than growing the port's own adapter (KOD-829).

The status update is addressed by the request's own key: the declared schema
takes the same forms the container read takes — a name, an id, an identifier
or a slug — and the scope reads address the container by that key on every
tick, so a read before the write would buy an identifier the declaration
already accepts.  Nothing of the answer is parsed: its shape is unmeasured,
and an adapter that read it would be asserting one.

There is no retry loop.  A create the transport wrote and never heard back
from may have landed, and a second attempt would post the report twice.
"""

from collections.abc import Mapping
from typing import Final

from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import McpToolCaller
from kodezart.domain.errors import ScopeStatusError, TransientAPIError
from kodezart.types.domain.scope import ScopeKind, ScopeRef

_TOOL_SAVE_STATUS_UPDATE = "save_status_update"

#: The tool's ``type`` value, which is also the argument carrying the target,
#: per scope kind.  A milestone and an issue are absent because the backend
#: holds no status update for either; a test pins this mapping against the
#: kinds the terminal posts for, so the two cannot drift apart.
_TARGET_ARGUMENT: Final[Mapping[ScopeKind, str]] = {
    ScopeKind.PROJECT: "project",
    ScopeKind.INITIATIVE: "initiative",
}


class LinearScopeStatusWriter:
    """Posts one status update on a project or an initiative."""

    def __init__(self, *, caller: McpToolCaller) -> None:
        self._caller: McpToolCaller = caller

    async def post_status_update(self, *, ref: ScopeRef, body: str) -> None:
        """Post *body* on *ref*'s container, or refuse before any call.

        Both refusals are knowable from the arguments alone — a kind with no
        status surface, and a body with nothing in it — so both are raised
        here rather than spent as a request that comes back as an opaque
        tool error.
        """
        argument = _TARGET_ARGUMENT.get(ref.kind)
        if argument is None:
            raise ScopeStatusError(
                ref=ref, reason="this scope kind carries no status update"
            )
        if not body.strip():
            raise ScopeStatusError(
                ref=ref, reason="a status update with no body states nothing"
            )
        try:
            await self._caller.call_tool(
                name=_TOOL_SAVE_STATUS_UPDATE,
                arguments={"type": argument, argument: ref.key, "body": body},
            )
        except McpCredentialRefusedError as exc:
            raise TrackerAccessDeniedError(str(exc)) from exc
        except (McpTransportError, TransientAPIError) as exc:
            raise TrackerUnavailableError(str(exc)) from exc
