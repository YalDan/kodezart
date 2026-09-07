"""``RunRecordSink`` over the knowledge vendor's MCP server.

A knowledge destination is a data source whose rows are pages. Scheduled
records retain their line-based contract. Fire records use the exact shared
run identity as their title and verify configured structured properties.
An existing session-created row is filled in place without touching prose.

Tool names and property payloads follow the vendor MCP server's OpenAPI:
``API-patch-page`` takes ``page_id`` and the properties to change; omitted
properties are preserved. The live schema supplies the title property and
valid select options. Structured verification reads the full matching page
set so duplicate identities cannot masquerade as a successful record.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from kodezart.adapters.notion_record_properties import mapped_outcome, mapping_error
from kodezart.core.errors import McpTransportError, RunRecordWriteError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import McpToolCaller
from kodezart.types.domain.notion_records import (
    NotionRecordPage,
    NotionRecordPageList,
    NotionRecordSchema,
)
from kodezart.types.domain.operation import RecordDestination, RunKind
from kodezart.types.domain.run_records import RunRecord, RunRecordFailure

_TOOL_RETRIEVE_DATA_SOURCE = "API-retrieve-a-data-source"
_TOOL_POST_PAGE = "API-post-page"
_TOOL_QUERY_DATA_SOURCE = "API-query-data-source"
_TOOL_PATCH_PAGE = "API-patch-page"


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

        Two conditions, and neither leans on the other: the vendor's OWN
        ``created_time`` inside the run's window, and the record's whole
        TITLE at the head of the title property every row of this data
        source carries.  The window alone answered for the whole log — two
        fires swept at one shutdown produced one row, the first answering
        for the second — and a title CONTAINING the name answered for
        every longer name it prefixed (KOD-288).  Page one of size one is
        all the answer needs.
        """
        if record.kind is RunKind.FIRE or destination.outcome_mapping is not None:
            title, column, option = await self._structured_target(destination, record)
            page = await self._find_record(destination, record, title)
            return page is not None and self._matches_outcome(
                page, title, record.title(), column, option
            )
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
                            "title": {"starts_with": record.title()},
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
        """Create or complete this run's page using its declared contract."""
        if record.kind is RunKind.FIRE or destination.outcome_mapping is not None:
            title, column, option = await self._structured_target(destination, record)
            page = await self._find_record(destination, record, title)
            if page is not None and self._matches_outcome(
                page, title, record.title(), column, option
            ):
                return
            properties: dict[str, object] = {
                title: {"title": [{"text": {"content": record.title()}}]},
                column: {"select": {"name": option}},
            }
            if page is not None:
                await self._caller.call_tool(
                    name=_TOOL_PATCH_PAGE,
                    arguments={"page_id": page.id, "properties": properties},
                )
            else:
                await self._caller.call_tool(
                    name=_TOOL_POST_PAGE,
                    arguments={
                        "parent": {
                            "type": "data_source_id",
                            "data_source_id": destination.id,
                        },
                        "properties": properties,
                    },
                )
            return
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

    async def _structured_target(
        self, destination: RecordDestination, record: RunRecord
    ) -> tuple[str, str, str]:
        payload = await self._caller.call_tool(
            name=_TOOL_RETRIEVE_DATA_SOURCE,
            arguments={"data_source_id": destination.id},
        )
        schema = self._validate(NotionRecordSchema, payload, _TOOL_RETRIEVE_DATA_SOURCE)
        titles = [
            name for name, value in schema.properties.items() if value.type == "title"
        ]
        if len(titles) != 1:
            raise mapping_error(
                destination, record, "the destination must have one title property"
            )
        column, option = mapped_outcome(destination, record, schema)
        return titles[0], column, option

    @staticmethod
    def _matches_outcome(
        page: NotionRecordPage, title: str, identity: str, column: str, option: str
    ) -> bool:
        heading = page.properties.get(title)
        if (
            heading is None
            or heading.title is None
            or "".join(part.plain_text for part in heading.title) != identity
        ):
            return False
        held = page.properties.get(column)
        return (
            held is not None and held.select is not None and held.select.name == option
        )

    async def _find_record(
        self, destination: RecordDestination, record: RunRecord, title_property: str
    ) -> NotionRecordPage | None:
        arguments: dict[str, object] = {
            "data_source_id": destination.id,
            "filter": {
                "and": [
                    {
                        "timestamp": "created_time",
                        "created_time": {"on_or_after": record.started_at.isoformat()},
                    },
                    {
                        "property": title_property,
                        "title": {"starts_with": record.title()},
                    },
                ]
            },
            "page_size": 100,
        }
        found: dict[str, NotionRecordPage] = {}
        cursors: set[str] = set()
        while True:
            payload = await self._caller.call_tool(
                name=_TOOL_QUERY_DATA_SOURCE, arguments=arguments
            )
            page = self._validate(
                NotionRecordPageList, payload, _TOOL_QUERY_DATA_SOURCE
            )
            for row in page.results:
                title = row.properties.get(title_property)
                if title is None or title.title is None:
                    raise mapping_error(
                        destination, record, "a queried row has no title to identify it"
                    )
                text = "".join(part.plain_text for part in title.title)
                if text == record.title() or text.startswith(f"{record.title()} — "):
                    found[row.id] = row
            if not page.has_more:
                break
            if not page.next_cursor or page.next_cursor in cursors:
                raise mapping_error(
                    destination, record, "record query pagination cannot advance"
                )
            cursors.add(page.next_cursor)
            arguments["start_cursor"] = page.next_cursor
        if len(found) > 1:
            raise RunRecordWriteError(
                "more than one destination row carries this run identity",
                kind=record.kind.value,
                destination=destination.id,
                system=destination.system.value,
                failure=RunRecordFailure.IDENTITY_CONFLICT.value,
            )
        return next(iter(found.values())) if found else None

    def _validate[T: BaseModel](self, model: type[T], payload: object, tool: str) -> T:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise McpTransportError(
                "the record destination returned an incomplete response",
                server_name=self._server_name,
                tool_name=tool,
            ) from exc

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
