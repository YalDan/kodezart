"""``RunRecordSink`` over the knowledge vendor's MCP server.

A destination in the KNOWLEDGE system is a data source — a database whose
rows are pages — and one structural record is one new page whose title
property carries the domain's one line.  Which property is the title is
the data source's own schema fact, so it is read rather than assumed:
every data source has exactly one title property, but its NAME is the
operator's ("Run" on the measured logs), and writing under a guessed name
is a vendor refusal.

The tool names are the vendor MCP server's OpenAPI-derived ones, held
here because they are vendor knowledge; the verification boot exercises
them against the live server, which is where a version mismatch fails
loudly (KOD-170).

The title property earns its schema read twice over: it is where a row is
written, and it is what a row is FOUND by — verification asks whether
this run's row is there, not whether the log has been written to lately
(KOD-288).
"""

from collections.abc import Mapping

from kodezart.core.errors import McpTransportError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolCaller
from kodezart.types.domain.operation import RecordDestination
from kodezart.types.domain.run_records import RunRecord

_TOOL_RETRIEVE_DATA_SOURCE = "API-retrieve-a-data-source"
_TOOL_POST_PAGE = "API-post-page"
_TOOL_QUERY_DATA_SOURCE = "API-query-data-source"


class NotionRecordSink:
    """Writes one row per record into a declared data source."""

    def __init__(self, *, caller: McpToolCaller, server_name: str) -> None:
        self._caller: McpToolCaller = caller
        self._server_name: str = server_name
        #: Title-property names by data source, resolved once per process:
        #: the schema is the vendor's and does not move under a running
        #: service, and re-reading it per row would double every write.
        self._title_properties: dict[str, str] = {}
        self._log: BoundLogger = get_logger(__name__)

    async def holds_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> bool:
        """Whether the data source holds a row about THIS run.

        Two conditions, because a run is identified by both: the vendor's
        OWN ``created_time`` inside the run's window, and the run's NAME
        in the title property every row of this data source carries.  The
        window alone answered for the whole log — two fires swept at one
        shutdown produced one row, the first answering for the second
        (KOD-288) — and the name alone would answer for the same issue's
        run last week.  Page one of size one is all the answer needs.
        """
        title_property = await self._title_property(destination)
        payload = await self._caller.call_tool(
            name=_TOOL_QUERY_DATA_SOURCE,
            arguments={
                "data_source_id": destination.id,
                "filter": {
                    "and": [
                        {
                            "timestamp": "created_time",
                            "created_time": {
                                "on_or_after": record.started_at.isoformat(),
                            },
                        },
                        {
                            "property": title_property,
                            "title": {"contains": record.name},
                        },
                    ],
                },
                "page_size": 1,
            },
        )
        if not isinstance(payload, Mapping):
            raise McpTransportError(
                "the data-source query answered with no object to read results from",
                server_name=self._server_name,
                tool_name=_TOOL_QUERY_DATA_SOURCE,
            )
        results = payload.get("results")
        if not isinstance(results, list):
            raise McpTransportError(
                "the data-source query's answer carries no results list",
                server_name=self._server_name,
                tool_name=_TOOL_QUERY_DATA_SOURCE,
            )
        return len(results) > 0

    async def write_record(
        self,
        *,
        destination: RecordDestination,
        record: RunRecord,
    ) -> None:
        """One page in the destination, its title property the record line."""
        title_property = await self._title_property(destination)
        await self._caller.call_tool(
            name=_TOOL_POST_PAGE,
            arguments={
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": destination.id,
                },
                "properties": {
                    title_property: {
                        "title": [{"text": {"content": record.line()}}],
                    },
                },
            },
        )

    async def _title_property(self, destination: RecordDestination) -> str:
        """The NAME of the destination's one title property, read once."""
        cached = self._title_properties.get(destination.id)
        if cached is not None:
            return cached
        payload = await self._caller.call_tool(
            name=_TOOL_RETRIEVE_DATA_SOURCE,
            arguments={"data_source_id": destination.id},
        )
        name = _title_property_of(payload)
        if name is None:
            raise McpTransportError(
                "the data source's schema names no title property; a row "
                "cannot be written without one",
                server_name=self._server_name,
                tool_name=_TOOL_RETRIEVE_DATA_SOURCE,
            )
        self._title_properties[destination.id] = name
        return name


def _title_property_of(payload: object) -> str | None:
    """The title property's name in a retrieve-a-data-source answer."""
    if not isinstance(payload, Mapping):
        return None
    properties = payload.get("properties")
    if not isinstance(properties, Mapping):
        return None
    for name, definition in properties.items():
        if isinstance(definition, Mapping) and definition.get("type") == "title":
            return str(name)
    return None
