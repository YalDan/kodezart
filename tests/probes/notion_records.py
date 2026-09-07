"""A persistent Notion page boundary for run-record conformance."""

from copy import deepcopy
from datetime import UTC, datetime


class NotionLogServer:
    def __init__(self):
        self.schema = {
            "properties": {
                "Run": {"type": "title"},
                "Disposition": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": name}
                            for name in ("Finished", "Failed", "PR opened")
                        ]
                    },
                },
                "What happened": {"type": "rich_text"},
            }
        }
        self.rows = {}
        self.calls = []
        self.now = datetime(2026, 9, 1, 12, tzinfo=UTC)

    def seed(self, title, *, properties=None):
        key = f"page-{len(self.rows) + 1}"
        self.rows[key] = {
            "id": key,
            "created_time": self.now.isoformat(),
            "properties": {
                "Run": {"title": [{"plain_text": title}]},
                **(properties or {}),
            },
        }
        return key

    @staticmethod
    def normalized(properties):
        result = deepcopy(properties)
        for value in result.values():
            for kind in ("title", "rich_text"):
                if kind in value:
                    value[kind] = [
                        {"plain_text": part["text"]["content"]} for part in value[kind]
                    ]
        return result

    async def call_tool(self, *, name, arguments):
        self.calls.append((name, deepcopy(dict(arguments))))
        if name == "API-retrieve-a-data-source":
            assert set(arguments) == {"data_source_id"}
            return deepcopy(self.schema)
        if name == "API-query-data-source":
            assert set(arguments) <= {
                "data_source_id",
                "filter",
                "page_size",
                "start_cursor",
            }
            clauses = arguments["filter"]["and"]
            started = datetime.fromisoformat(clauses[0]["created_time"]["on_or_after"])
            column = clauses[1]["property"]
            prefix = clauses[1]["title"]["starts_with"]
            rows = [
                row
                for row in self.rows.values()
                if datetime.fromisoformat(row["created_time"]) >= started
                and "".join(
                    part["plain_text"] for part in row["properties"][column]["title"]
                ).startswith(prefix)
            ]
            offset = int(arguments.get("start_cursor", "0"))
            end = offset + arguments["page_size"]
            return {
                "results": deepcopy(rows[offset:end]),
                "has_more": end < len(rows),
                "next_cursor": str(end) if end < len(rows) else None,
            }
        if name == "API-post-page":
            assert set(arguments) == {"parent", "properties"}
            values = self.normalized(arguments["properties"])
            key = self.seed("temporary")
            self.rows[key]["properties"] = values
            return deepcopy(self.rows[key])
        if name == "API-patch-page":
            assert set(arguments) == {"page_id", "properties"}
            row = self.rows[arguments["page_id"]]
            row["properties"].update(self.normalized(arguments["properties"]))
            return deepcopy(row)
        raise AssertionError(f"unexpected Notion tool: {name}")

    def writes(self):
        return [
            call
            for call in self.calls
            if call[0] in {"API-post-page", "API-patch-page"}
        ]
