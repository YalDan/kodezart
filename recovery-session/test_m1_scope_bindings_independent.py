"""Independent external-MCP controls for native scope identity and namespace reads."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import ScopeReadError
from tests.tracker.conftest import fixture_server, linear_over_fake_mcp
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_native_approval_aliases import ApprovalAliasServer, NATIVE_UUID
from tests.tracker.test_scope_label_mappings import APPROVAL, LABEL_CREATORS, SCOPE_LABELS


@pytest.mark.parametrize("foreign", [False, True])
async def test_display_address_cannot_borrow_an_unrelated_native_uuid(monkeypatch, foreign):
    server = ApprovalAliasServer(reported_uuid=NATIVE_UUID)
    original = server.call_tool

    async def call_tool(*, name, arguments):
        payload = await original(name=name, arguments=arguments)
        if name == "get_issue":
            payload = {**payload, "uuid": NATIVE_UUID}
            if foreign:
                payload["id"] = "FOREIGN-1"
        return payload

    monkeypatch.setattr(server, "call_tool", call_tool)
    tracker = tracker_over(server, scope_labels={"approved": "execution-consent"})
    if foreign:
        with pytest.raises(ScopeReadError, match="identity"):
            await tracker.execution_approved(issue_key="ROOT-1")
    else:
        assert await tracker.execution_approved(issue_key="ROOT-1") is True
    assert server.tool_calls("save_issue") == []
    assert server.tool_calls("list_issues") == []


async def test_actual_uuid_alias_approval_revocation_is_reread_on_same_adapter():
    server = ApprovalAliasServer(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server, scope_labels={"approved": "execution-consent"})
    for expected in (True, False, True):
        server.issues["ROOT-1"].labels = ["execution-consent"] if expected else []
        assert await tracker.execution_approved(issue_key=NATIVE_UUID) is expected
    assert server.tool_calls("get_issue") == [
        {"id": NATIVE_UUID, "includeRelations": True}
    ] * 3
    assert not any(name.startswith("save_") for name, _ in server.calls)


@pytest.mark.parametrize("omit_pagination", [False, True])
async def test_missing_project_namespace_metadata_never_means_absent_label(
    monkeypatch, omit_pagination
):
    server = fixture_server()
    name = SCOPE_LABELS["approved"]
    server.labels.append(name)
    server.project_labels.append(name)
    server.initiative_labels.append(name)
    original = server.call_tool

    async def call_tool(*, name, arguments):
        payload = await original(name=name, arguments=arguments)
        if name == "list_project_labels" and omit_pagination:
            payload = {key: value for key, value in payload.items() if key != "hasNextPage"}
        return payload

    monkeypatch.setattr(server, "call_tool", call_tool)
    tracker = linear_over_fake_mcp(server)
    if omit_pagination:
        with pytest.raises(TrackerProtocolError):
            await tracker.ensure_mappings(refs=[APPROVAL])
    else:
        (outcome,) = await tracker.ensure_mappings(refs=[APPROVAL])
        assert outcome.action.value == "adopted"
    assert not any(tool in LABEL_CREATORS for tool, _ in server.calls)
    assert not any(tool in {"save_issue", "save_project", "update_initiative"} for tool, _ in server.calls)
