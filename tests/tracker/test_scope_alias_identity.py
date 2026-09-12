"""An initial opaque UUID lookup may normalize to its human issue key."""

from collections.abc import Mapping

from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_scope_reads import ROOT, ScopeMcpServer

UUID = "00000000-0000-4000-8000-000000000073"


class _UuidLookup(ScopeMcpServer):
    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "get_issue" and arguments.get("id") == UUID:
            payload = await super().call_tool(
                name=name, arguments={**arguments, "id": ROOT.key}
            )
            assert isinstance(payload, Mapping)
            return {**payload, "uuid": UUID}
        return await super().call_tool(name=name, arguments=arguments)


async def test_supported_initial_uuid_lookup_preserves_canonical_subtree():
    tracker = linear_over_fake_mcp(_UuidLookup())
    rows = await tracker.scope_issues(ref=ScopeRef(kind=ScopeKind.ISSUE, key=UUID))
    assert {row.issue_key for row in rows} == {ROOT.key, "FIX-2", "FIX-3"}
