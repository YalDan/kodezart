"""``RunRecordSink`` over the tracker's MCP server.

A destination in the TRACKER system is a document, and one structural
record is one line appended to it — the same shape the scan checkpoint
carries, so a tracker-side log reads as a plain dated list.  The append
rides the vendor's patch operation rather than a read-modify-write of the
whole content: two writers appending concurrently must not lose each
other's lines (KOD-170).

Verification reads the vendor's OWN clock: the document's ``updatedAt``
moves on every write, so a document touched at or after a run's start
holds that run's row — whatever prose the session appended — without this
adapter parsing anyone's line format.
"""

from datetime import datetime

from kodezart.core.errors import McpTransportError
from kodezart.core.protocols import McpToolCaller
from kodezart.types.domain.operation import RecordDestination
from kodezart.types.domain.run_records import RunRecord

_TOOL_SAVE_DOCUMENT = "save_document"
_TOOL_GET_DOCUMENT = "get_document"


class LinearRecordSink:
    """Appends one line per record to a declared tracker document."""

    def __init__(self, *, caller: McpToolCaller, server_name: str) -> None:
        self._caller: McpToolCaller = caller
        self._server_name: str = server_name

    async def has_record_since(
        self,
        *,
        destination: RecordDestination,
        since: datetime,
    ) -> bool:
        """Whether the document was written at or after *since*.

        ``updatedAt`` is the vendor's timestamp for the LAST write, which
        is exactly the question: a run whose session appended its row
        moved it past the run's start, and one that skipped left it
        behind (measured live on the scan checkpoint, 2026-09-01).
        """
        payload = await self._caller.call_tool(
            name=_TOOL_GET_DOCUMENT,
            arguments={"id": destination.id},
        )
        if not isinstance(payload, dict):
            raise McpTransportError(
                "the document read answered with no object to read updatedAt from",
                server_name=self._server_name,
                tool_name=_TOOL_GET_DOCUMENT,
            )
        updated_at = payload.get("updatedAt")
        if not isinstance(updated_at, str):
            raise McpTransportError(
                "the document read carries no updatedAt timestamp",
                server_name=self._server_name,
                tool_name=_TOOL_GET_DOCUMENT,
            )
        try:
            updated = datetime.fromisoformat(updated_at)
        except ValueError as exc:
            raise McpTransportError(
                f"the document's updatedAt is not a readable timestamp: {updated_at!r}",
                server_name=self._server_name,
                tool_name=_TOOL_GET_DOCUMENT,
            ) from exc
        return updated >= since

    async def write_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> None:
        """Append the record's line to the destination document."""
        await self._caller.call_tool(
            name=_TOOL_SAVE_DOCUMENT,
            arguments={
                "id": destination.id,
                "patch": [{"op": "append", "text": f"\n{record.line()}"}],
            },
        )
