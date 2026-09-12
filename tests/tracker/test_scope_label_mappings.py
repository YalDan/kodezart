"""Scope labels are instated without granting approval (KOD-378, KOD-379)."""

from collections.abc import Mapping
from itertools import product

import pytest

from kodezart.adapters.linear_mcp_types import LinearLabelListWire
from kodezart.core.errors import (
    TrackerEnsureConflictError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.protocols import TrackerPort
from kodezart.services.tracker_boot import (
    configured_mappings,
    owned_mappings,
    reconcile_tracker_mappings,
)
from kodezart.types.domain.operation import TeamEntry
from kodezart.types.domain.tracker import EnsureAction, MappingKind, MappingRef
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    fixture_server,
    linear_over_fake_mcp,
)
from tests.tracker.connected_app_label_contract import (
    CONNECTED_APP_LABEL_TOOLS,
    CONNECTED_INITIATIVE_LABELS,
    CONNECTED_ISSUE_LABELS,
    CONNECTED_PROJECT_LABELS,
)
from tests.tracker.test_linear_tool_roster import LIVE_TOOL_ROSTER
from tests.tracker.test_tracker_boot import operation_config

SCOPE_LABELS = {
    "triage": "custom:scope-intake",
    "proposed": "custom:scope-draft",
    "approved": "custom:scope-authorized",
    "paused": "custom:scope-paused",
}
APPROVAL = MappingRef(
    kind=MappingKind.SCOPE_LABEL,
    name="approved",
    identifier=SCOPE_LABELS["approved"],
)
LABEL_CREATORS = frozenset(
    {"create_issue_label", "save_project_label", "create_initiative_label"},
)


def test_scope_mappings_are_owned_once_regardless_of_declared_teams() -> None:
    config = operation_config().model_copy(
        update={
            "scope_labels": dict(SCOPE_LABELS),
            "teams": {
                "one": TeamEntry(name="First", key="ONE"),
                "two": TeamEntry(name="Second", key="TWO"),
            },
        },
    )
    expected = tuple(
        MappingRef(kind=MappingKind.SCOPE_LABEL, name=name, identifier=identifier)
        for name, identifier in sorted(SCOPE_LABELS.items())
    )
    assert (
        tuple(
            ref
            for ref in configured_mappings(config)
            if ref.kind is MappingKind.SCOPE_LABEL
        )
        == expected
    )
    assert (
        tuple(
            ref for ref in owned_mappings(config) if ref.kind is MappingKind.SCOPE_LABEL
        )
        == expected
    )


async def test_scope_instate_and_repeat_preserve_issue_approval(
    tracker: TrackerPort,
) -> None:
    before = {
        key: await tracker.read_issue(issue_key=key)
        for key in (CLAIMED_ISSUE, APPROVED_ISSUE)
    }
    assert await tracker.resolve_mappings(refs=[APPROVAL]) == (APPROVAL,)
    (created,) = await tracker.ensure_mappings(refs=[APPROVAL])
    assert created.ref == APPROVAL
    assert created.identifier == SCOPE_LABELS["approved"]
    assert created.action is EnsureAction.CREATED
    assert await tracker.resolve_mappings(refs=[APPROVAL]) == ()
    (adopted,) = await tracker.ensure_mappings(refs=[APPROVAL])
    assert adopted.action is EnsureAction.ADOPTED
    assert {key: await tracker.read_issue(issue_key=key) for key in before} == before


async def test_scope_labels_boot_with_configured_custom_and_extra_names(
    tracker: TrackerPort,
) -> None:
    config = operation_config().model_copy(
        update={"scope_labels": dict(SCOPE_LABELS), "documents": {}},
    )
    result = await reconcile_tracker_mappings(tracker=tracker, config=config)
    assert result.config.scope_labels == SCOPE_LABELS
    assert {
        outcome.ref.identifier
        for outcome in result.outcomes
        if outcome.ref.kind is MappingKind.SCOPE_LABEL
        and outcome.action is EnsureAction.CREATED
    } == set(SCOPE_LABELS.values())
    assert (
        await tracker.resolve_mappings(
            refs=[
                ref
                for ref in configured_mappings(config)
                if ref.kind is MappingKind.SCOPE_LABEL
            ],
        )
        == ()
    )
    repeated = await reconcile_tracker_mappings(tracker=tracker, config=result.config)
    assert repeated.config == result.config
    assert all(outcome.action is EnsureAction.ADOPTED for outcome in repeated.outcomes)


@pytest.mark.parametrize("identifier", [None, SCOPE_LABELS["approved"]])
async def test_scope_refs_cannot_request_a_team_copy(
    tracker: TrackerPort,
    identifier: str | None,
) -> None:
    ref = MappingRef(
        kind=MappingKind.SCOPE_LABEL,
        name="approved",
        identifier=identifier,
        scope="fixture-team",
    )
    with pytest.raises(TrackerEnsureConflictError, match="workspace scope"):
        await tracker.ensure_mappings(refs=[ref])
    assert await tracker.resolve_mappings(refs=[ref]) == (ref,)


async def test_scope_ref_requires_an_identifier(tracker: TrackerPort) -> None:
    ref = MappingRef(kind=MappingKind.SCOPE_LABEL, name="approved")
    with pytest.raises(TrackerEnsureConflictError, match="identifier"):
        await tracker.ensure_mappings(refs=[ref])


async def test_team_queue_label_does_not_resolve_a_workspace_scope_label(
    tracker: TrackerPort,
) -> None:
    queue = APPROVAL.model_copy(
        update={"kind": MappingKind.QUEUE_STATE, "scope": "fixture-team"},
    )
    await tracker.ensure_mappings(refs=[queue])
    assert await tracker.resolve_mappings(refs=[APPROVAL]) == (APPROVAL,)
    with pytest.raises(TrackerEnsureConflictError, match="fixture-team"):
        await tracker.ensure_mappings(refs=[APPROVAL])


@pytest.mark.parametrize(
    ("issue", "project", "initiative"), product((False, True), repeat=3)
)
async def test_only_missing_native_namespaces_are_created(
    *,
    issue: bool,
    project: bool,
    initiative: bool,
) -> None:
    name = SCOPE_LABELS["approved"]
    server = fixture_server()
    original = {key: tuple(entity.labels) for key, entity in server.issues.items()}
    if issue:
        server.labels.append(name)
    if project:
        server.project_labels.append(name)
    if initiative:
        server.initiative_labels.append(name)
    tracker = linear_over_fake_mcp(server)
    assert await tracker.resolve_mappings(refs=[APPROVAL]) == (
        () if issue and project and initiative else (APPROVAL,)
    )
    (outcome,) = await tracker.ensure_mappings(refs=[APPROVAL])
    expected = {
        tool
        for tool, exists in (
            ("create_issue_label", issue),
            ("save_project_label", project),
            ("create_initiative_label", initiative),
        )
        if not exists
    }
    writes = [(tool, args) for tool, args in server.calls if tool in LABEL_CREATORS]
    assert {tool for tool, _ in writes} == expected
    assert len(writes) == len(expected)
    assert all(args == {"name": name} for _, args in writes)
    assert outcome.action is (
        EnsureAction.CREATED if expected else EnsureAction.ADOPTED
    )
    assert all(
        name in names
        for names in (server.labels, server.project_labels, server.initiative_labels)
    )
    assert {
        key: tuple(entity.labels) for key, entity in server.issues.items()
    } == original
    assert all(
        tool in LABEL_CREATORS or tool.startswith("list_") for tool, _ in server.calls
    )
    server.calls.clear()
    await tracker.ensure_mappings(refs=[APPROVAL])
    assert all(tool.startswith("list_") for tool, _ in server.calls)


async def test_label_definition_pagination_prevents_duplicate_creation() -> None:
    name = SCOPE_LABELS["approved"]
    server = fixture_server()
    server.label_page_size = 1
    server.labels.extend(["another-issue-label", name])
    server.project_labels.extend(["another-project-label", name])
    server.initiative_labels.extend(["another-initiative-label", name])
    tracker = linear_over_fake_mcp(server)
    (outcome,) = await tracker.ensure_mappings(refs=[APPROVAL])
    assert outcome.action is EnsureAction.ADOPTED
    assert all(tool.startswith("list_") for tool, _ in server.calls)
    for tool in ("list_issue_labels", "list_project_labels", "list_initiative_labels"):
        assert any("cursor" in args for args in server.tool_calls(tool))


@pytest.mark.parametrize("workspace_copy", [False, True])
@pytest.mark.parametrize("page_size", [None, 1])
async def test_team_collision_refuses_all_namespace_writes(
    workspace_copy: bool,
    page_size: int | None,
) -> None:
    name = SCOPE_LABELS["approved"]
    server = fixture_server()
    server.label_page_size = page_size
    server.team_labels["fixture-team-id"] = [name]
    if workspace_copy:
        server.labels.append(name)
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerEnsureConflictError, match="fixture-team"):
        await tracker.ensure_mappings(refs=[APPROVAL])
    assert all(tool.startswith("list_") for tool, _ in server.calls)
    assert server.team_labels["fixture-team-id"] == [name]
    assert server.project_labels == server.initiative_labels == []
    if page_size is not None:
        assert any(
            args.get("team") == "fixture-team" and "cursor" in args
            for args in server.tool_calls("list_issue_labels")
        )


async def test_unavailable_project_creator_is_a_typed_failure_without_fallback() -> (
    None
):
    server = fixture_server(scope_refusals={"save_project_label": "Unknown tool"})
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerUnavailableError, match="save_project_label"):
        await tracker.ensure_mappings(refs=[APPROVAL])
    assert server.tool_calls("save_project_label") == [
        {"name": SCOPE_LABELS["approved"]}
    ]
    assert server.project_labels == server.initiative_labels == []
    assert all(
        tool in LABEL_CREATORS or tool.startswith("list_") for tool, _ in server.calls
    )


class MissingReadbackServer(FakeLinearMcpServer):
    project_write_visible = False

    def _tool_save_project_label(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        if self.project_write_visible:
            return super()._tool_save_project_label(arguments)
        return {}


async def test_missing_readback_fails_and_a_later_boot_completes_missing_labels() -> (
    None
):
    server = MissingReadbackServer(teams=["fixture-team"])
    with pytest.raises(TrackerProtocolError, match="readback"):
        await linear_over_fake_mcp(server).ensure_mappings(refs=[APPROVAL])
    assert server.labels == [SCOPE_LABELS["approved"]]
    assert server.project_labels == server.initiative_labels == []

    server.project_write_visible = True
    server.calls.clear()
    (recovered,) = await linear_over_fake_mcp(server).ensure_mappings(refs=[APPROVAL])
    assert recovered.action is EnsureAction.CREATED
    assert [tool for tool, _ in server.calls if tool in LABEL_CREATORS] == [
        "save_project_label",
        "create_initiative_label",
    ]
    assert all(
        tool in LABEL_CREATORS or tool.startswith("list_") for tool, _ in server.calls
    )


class BrokenCursorServer(FakeLinearMcpServer):
    continuation_cursor: str | None = None

    def _tool_list_project_labels(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        response: dict[str, object] = {"labels": [], "hasNextPage": True}
        if self.continuation_cursor is not None:
            response["cursor"] = self.continuation_cursor
        return response


@pytest.mark.parametrize("cursor", [None, "repeated"])
async def test_a_truncated_namespace_cannot_be_treated_as_absence(
    cursor: str | None,
) -> None:
    server = BrokenCursorServer(teams=["fixture-team"])
    server.continuation_cursor = cursor
    with pytest.raises(TrackerProtocolError, match="continuation cursor"):
        await linear_over_fake_mcp(server).ensure_mappings(refs=[APPROVAL])
    assert all(tool.startswith("list_") for tool, _ in server.calls)
    assert len(server.tool_calls("list_project_labels")) == (1 if cursor is None else 2)


def test_connected_read_captures_do_not_claim_service_creator_validation() -> None:
    issue = LinearLabelListWire.model_validate(CONNECTED_ISSUE_LABELS)
    initiative = LinearLabelListWire.model_validate(CONNECTED_INITIATIVE_LABELS)
    project = LinearLabelListWire.model_validate(CONNECTED_PROJECT_LABELS)
    assert issue.labels[0].name == initiative.labels[0].name
    assert issue.labels[0].id != initiative.labels[0].id
    assert project.labels == []
    assert not any(page.has_next_page for page in (issue, initiative, project))
    assert "save_project_label" in CONNECTED_APP_LABEL_TOOLS
    assert "save_project_label" not in LIVE_TOOL_ROSTER
