"""``ScopeStatusUpdates`` over the tracker's MCP server.

One class for one role over the caller the tracker already dials, following
the record sink rather than growing the port's own adapter (KOD-829).  The
role is one surface read and then written: the scope terminal asks what the
container already carries before it posts, so both calls belong to the same
consumer and the same session.

The status update is addressed by the request's own key: the declared schema
takes the same forms the container read takes — a name, an id, an identifier
or a slug — and the scope reads address the container by that key on every
tick, so a read before the write would buy an identifier the declaration
already accepts.

Nothing of the WRITE's answer is parsed: its shape is unmeasured, and an
adapter that read it would be asserting one.  The READ's answer is parsed,
and exactly two fields of each item are — the body and its creation instant,
in the envelope measured on 2026-09-21.

There is no retry loop.  A create the transport wrote and never heard back
from may have landed, and a second attempt would post the report twice.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import ValidationError

from kodezart.adapters.linear.status_update_types import LinearStatusUpdatesWire
from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import McpToolCaller
from kodezart.domain.errors import ScopeStatusError, TransientAPIError
from kodezart.types.domain.scope import ScopeKind, ScopeRef

_TOOL_SAVE_STATUS_UPDATE = "save_status_update"
_TOOL_GET_STATUS_UPDATES = "get_status_updates"

#: The tool's ``type`` value, which is also the argument carrying the target,
#: per scope kind.  A milestone and an issue are absent because the backend
#: holds no status update for either; a test pins this mapping against the
#: kinds the terminal posts for, so the two cannot drift apart.
_TARGET_ARGUMENT: Final[Mapping[ScopeKind, str]] = {
    ScopeKind.PROJECT: "project",
    ScopeKind.INITIATIVE: "initiative",
}


class LinearScopeStatusUpdates:
    """One read and one write on a project's or an initiative's status updates."""

    def __init__(self, *, caller: McpToolCaller) -> None:
        self._caller: McpToolCaller = caller

    async def post_status_update(self, *, ref: ScopeRef, body: str) -> None:
        """Post *body* on *ref*'s container, or refuse before any call.

        Both refusals are knowable from the arguments alone — a kind with no
        status surface, and a body with nothing in it — so both are raised
        here rather than spent as a request that comes back as an opaque
        tool error.
        """
        argument = self._target(ref)
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

    async def status_update_bodies(self, *, ref: ScopeRef) -> Sequence[str]:
        """The container's status update bodies, newest first.

        One page of the tool's own default size, ordered here by each item's
        creation instant rather than by the position the answer arrived in:
        the declaration names a default order, and an adapter that relied on
        it would be comparing against whichever item the backend chose to
        list first.

        No ``user`` filter and no cursor: which reports are this operation's
        is answered by the body's own heading, and a container whose newest
        page of updates carries none of them is a container this report has
        not been posted on.
        """
        argument = self._target(ref)
        try:
            payload = await self._caller.call_tool(
                name=_TOOL_GET_STATUS_UPDATES,
                arguments={
                    "type": argument,
                    argument: ref.key,
                    "orderBy": "createdAt",
                },
            )
        except McpCredentialRefusedError as exc:
            raise TrackerAccessDeniedError(str(exc)) from exc
        except (McpTransportError, TransientAPIError) as exc:
            raise TrackerUnavailableError(str(exc)) from exc
        try:
            listing = LinearStatusUpdatesWire.model_validate(payload)
        except ValidationError as exc:
            raise TrackerProtocolError(
                "the status update listing does not match its declared shape",
                tool=_TOOL_GET_STATUS_UPDATES,
                detail=str(exc),
            ) from exc
        return tuple(
            item.body
            for item in sorted(
                listing.status_updates,
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def _target(self, ref: ScopeRef) -> str:
        """The argument name carrying *ref*'s container, or refuse.

        Asked by both calls and before either spends a request: a scope kind
        the backend holds no status update for has nothing to read and
        nothing to write, and that is one fact about the kind rather than
        two facts about two calls.
        """
        argument = _TARGET_ARGUMENT.get(ref.kind)
        if argument is None:
            raise ScopeStatusError(
                ref=ref, reason="this scope kind carries no status update"
            )
        return argument
