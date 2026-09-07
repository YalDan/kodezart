"""Scope calls respect the connected-app declarations read on 2026-09-07.

These declarations supplement the August service capture. Exercising them
over a double checks request construction, not service-credential access.
"""

import pytest

from kodezart.domain.errors import ScopeReadError
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_linear_tool_arguments import LIVE_INPUT_SCHEMAS, ToolSchema
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ROOT,
    ScopeMcpServer,
)

SCOPE_INPUT_SCHEMAS = {
    "get_issue": LIVE_INPUT_SCHEMAS["get_issue"],
    "list_issues": LIVE_INPUT_SCHEMAS["list_issues"],
    "get_project": ToolSchema(
        frozenset({"query", "includeMembers", "includeMilestones", "includeResources"}),
        frozenset({"query"}),
    ),
    "get_initiative": ToolSchema(
        frozenset({"query", "includeProjects", "includeSubInitiatives"}),
        frozenset({"query"}),
    ),
    "get_milestone": ToolSchema(
        frozenset({"project", "query"}), frozenset({"project", "query"})
    ),
    "list_milestones": ToolSchema(frozenset({"project"}), frozenset({"project"})),
    "list_projects": ToolSchema(
        frozenset(
            {
                "createdAt",
                "cursor",
                "fields",
                "includeArchived",
                "includeMembers",
                "includeMilestones",
                "initiative",
                "label",
                "limit",
                "member",
                "orderBy",
                "query",
                "state",
                "team",
                "updatedAt",
            }
        ),
        frozenset(),
    ),
}


async def test_scope_reads_send_declared_arguments_on_every_call() -> None:
    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server)
    for ref in (ROOT, PROJECT, MILESTONE, INITIATIVE):
        await tracker.scope_issues(ref=ref)
    for ref in (PROJECT, INITIATIVE):
        await tracker.container_metadata(ref=ref)
    with pytest.raises(ScopeReadError):
        await tracker.container_metadata(ref=MILESTONE)

    assert {tool for tool, _ in server.calls} == set(SCOPE_INPUT_SCHEMAS)
    for tool, arguments in server.calls:
        schema = SCOPE_INPUT_SCHEMAS[tool]
        assert set(arguments) <= schema.properties, (tool, arguments)
        assert schema.required <= set(arguments), (tool, arguments)
        if tool in {"list_projects", "list_issues"}:
            maximum = 50 if tool == "list_projects" else 250
            assert 1 <= arguments["limit"] <= maximum
            assert arguments["includeArchived"] is True
    assert any("cursor" in arguments for _, arguments in server.calls)
