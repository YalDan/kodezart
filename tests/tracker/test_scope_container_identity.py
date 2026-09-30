"""A top-level container answer carries two identities; the UUID is the one kept.

Measured 2026-09-24 against the connected Linear MCP with the service
credential, on the first live scope read: ``get_project``, ``list_projects``,
``get_initiative`` and ``list_initiatives`` answer with a display identifier
(``P-DUC-33``, ``I-4``) under ``id`` and the UUID under ``uuid``, while every
payload that references a container — an issue's ``projectId``, an
initiative's ``projects``, a project's ``initiatives``, a milestone listing —
carries the UUID alone.  Before this was accounted for, every project scope
read refused with "issue moved out of the listed project" and every approval
read with "container approval identity changed", on a board nobody touched.
"""

from collections.abc import Mapping

from kodezart.adapters.linear.scope_reader import LinearScopeReader
from kodezart.adapters.linear.scope_types import (
    LinearScopeInitiativeWire,
    LinearScopeProjectWire,
)
from kodezart.core.protocols import McpToolResult
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    PROJECT,
    ROOT,
    ScopeMcpServer,
    _container,
)

IDENTIFIERS = {PROJECT.key: "P-DUC-33", INITIATIVE.key: "I-4"}
APPROVED = "scope:approved"


def _measured(payload: Mapping[str, object]) -> dict[str, object]:
    """One top-level answer as measured: identifier under id, UUID under uuid."""
    key = str(payload["id"])
    return {**payload, "id": IDENTIFIERS.get(key, key.upper()), "uuid": key}


class _MeasuredServer(ScopeMcpServer):
    """The fixture's containers, answered the way the connected server answers now.

    Only top-level answers change shape.  Nested references stay as the
    fixture holds them, which is the UUID alone — exactly the asymmetry the
    live server shows.
    """

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        payload = await super().call_tool(name=name, arguments=arguments)
        if name in {"get_project", "get_initiative"}:
            assert isinstance(payload, Mapping)
            return _measured(payload)
        if name == "list_projects":
            assert isinstance(payload, Mapping)
            projects = payload["projects"]
            assert isinstance(projects, list)
            return {**payload, "projects": [_measured(entry) for entry in projects]}
        return payload


async def test_project_members_are_read_when_the_answer_carries_both_identities() -> (
    None
):
    tracker = linear_over_fake_mcp(_MeasuredServer())

    issues = await tracker.scope_issues(ref=PROJECT)

    assert {issue.issue_key for issue in issues} == {ROOT.key, "FIX-2", "FIX-4"}


async def test_initiative_members_are_read_through_its_listed_projects() -> None:
    tracker = linear_over_fake_mcp(_MeasuredServer())

    issues = await tracker.scope_issues(ref=INITIATIVE)

    assert {issue.issue_key for issue in issues} == {
        ROOT.key,
        "FIX-2",
        "FIX-3",
        "FIX-4",
        "FIX-5",
    }


async def test_container_metadata_keeps_the_uuid_as_the_canonical_reference() -> None:
    tracker = linear_over_fake_mcp(_MeasuredServer())

    container = await tracker.container_metadata(ref=PROJECT)

    assert container == _container(PROJECT, INITIATIVE)


async def test_the_approval_read_matches_the_uuid_it_was_asked_for() -> None:
    server = _MeasuredServer()
    server.projects[PROJECT.key] = {
        **server.projects[PROJECT.key],
        "labels": [APPROVED],
    }
    tracker = linear_over_fake_mcp(server)

    async def call(name: str, arguments: Mapping[str, object]) -> McpToolResult:
        return await server.call_tool(name=name, arguments=arguments)

    reader = LinearScopeReader(call=call, read_issue=tracker.read_issue)

    approved, parent = await reader.approval_parent(
        ref=PROJECT, approved_label=APPROVED
    )

    assert approved
    assert parent == INITIATIVE


def test_an_answer_without_uuid_keeps_the_id_it_came_with() -> None:
    body: dict[str, object] = {"name": "n", "description": None, "labels": []}

    project = LinearScopeProjectWire.model_validate(
        {**body, "id": "project-one", "initiatives": [{"id": "initiative-one"}]}
    )
    initiative = LinearScopeInitiativeWire.model_validate(
        {
            **body,
            "id": "I-4",
            "uuid": "initiative-one",
            "parentInitiatives": [],
            "subInitiatives": [{"id": "initiative-two"}],
        }
    )

    assert project.id == "project-one"
    assert [item.id for item in project.initiatives] == ["initiative-one"]
    assert initiative.id == "initiative-one"
    assert [item.id for item in initiative.sub_initiatives] == ["initiative-two"]
