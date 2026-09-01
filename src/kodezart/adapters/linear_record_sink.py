"""``RunRecordSink`` over the tracker's MCP server.

A destination in the TRACKER system is a document, and one structural
record is one line appended to it — the same shape the scan checkpoint
carries, so a tracker-side log reads as a plain dated list.  The append
rides the vendor's patch operation rather than a read-modify-write of the
whole content: two writers appending concurrently must not lose each
other's lines (KOD-170).
"""

from kodezart.core.protocols import McpToolCaller
from kodezart.types.domain.operation import RecordDestination
from kodezart.types.domain.run_records import RunRecord

_TOOL_SAVE_DOCUMENT = "save_document"


class LinearRecordSink:
    """Appends one line per record to a declared tracker document."""

    def __init__(self, *, caller: McpToolCaller) -> None:
        self._caller: McpToolCaller = caller

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
