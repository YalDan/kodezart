"""Native milestone listing preserves complete, stable container identity."""

import pytest


async def test_project_milestones_expose_complete_native_identities_and_descriptions():
    from tests.tracker.conftest import linear_over_fake_mcp
    from tests.tracker.test_scope_reads import MILESTONE, PROJECT, ScopeMcpServer

    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server)
    milestones = await tracker.project_milestones(project_key=PROJECT.key)
    assert {item.ref.key for item in milestones} == {
        row["id"] for row in server.milestones[PROJECT.key]
    }
    assert all(item.parent == PROJECT and item.url is None for item in milestones)
    assert (
        next(item for item in milestones if item.ref == MILESTONE).description
        == f"Complete description of {MILESTONE.key}"
    )


@pytest.mark.parametrize("fault", ["duplicate", "wrong_detail", "changed_membership"])
async def test_project_milestone_incomplete_identity_evidence_refuses(
    monkeypatch, fault
):
    from kodezart.domain.errors import ScopeReadError
    from tests.tracker.conftest import linear_over_fake_mcp
    from tests.tracker.test_scope_reads import PROJECT, ScopeMcpServer

    server = ScopeMcpServer()
    original = server.call_tool
    calls = 0

    async def call_tool(*, name, arguments):
        nonlocal calls
        result = await original(name=name, arguments=arguments)
        if name == "list_milestones":
            calls += 1
            if fault == "duplicate":
                result = {
                    **result,
                    "milestones": [*result["milestones"], result["milestones"][0]],
                }
            if fault == "changed_membership" and calls == 2:
                result = {**result, "milestones": []}
        if name == "get_milestone" and fault == "wrong_detail":
            result = {**result, "id": "another-native-id"}
        return result

    monkeypatch.setattr(server, "call_tool", call_tool)
    with pytest.raises(ScopeReadError):
        await linear_over_fake_mcp(server).project_milestones(project_key=PROJECT.key)
