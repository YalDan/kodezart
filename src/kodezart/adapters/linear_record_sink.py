"""``RunRecordSink`` over the tracker's MCP server.

A destination in the TRACKER system is a document, and one structural
record is one line appended to it — the same shape the scan checkpoint
carries, so a tracker-side log reads as a plain dated list.  The append
rides the vendor's patch operation rather than a read-modify-write of the
whole content: two writers appending concurrently must not lose each
other's lines (KOD-170).

Verification reads the document itself and looks for THIS run's row.  The
document's ``updatedAt`` was the earlier answer — it moves on every write,
so any run in the window answered for every other, and two fires swept at
one shutdown produced one row (KOD-288).  What identifies a run in a log
of runs is its NAME: the issue a fire ran on, the pass a scheduled run is.
"""

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

    async def holds_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> bool:
        """Whether the document already carries a row about THIS run.

        The document is the log, so the log is read: a row naming this
        run is this run's record, whatever prose surrounds it, and a row
        naming another run is another run's — which is the whole of what
        "any row since" could not tell apart (KOD-288).
        """
        payload = await self._caller.call_tool(
            name=_TOOL_GET_DOCUMENT,
            arguments={"id": destination.id},
        )
        if not isinstance(payload, dict):
            raise McpTransportError(
                "the document read answered with no object to read content from",
                server_name=self._server_name,
                tool_name=_TOOL_GET_DOCUMENT,
            )
        content = payload.get("content")
        if not isinstance(content, str):
            raise McpTransportError(
                "the document read carries no content to search for the run's row",
                server_name=self._server_name,
                tool_name=_TOOL_GET_DOCUMENT,
            )
        return record.name in content

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
