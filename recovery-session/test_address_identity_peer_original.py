"""Actual alias entry cannot borrow approval from a swapped native response."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import CriterionReadError, FireSpecEntryError, ScopeReadError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.tracker.conftest import FIRE_SCOPE_LABEL
from tests.tracker.test_linear_mcp_tracker import tracker_over
from tests.tracker.test_native_approval_aliases import (
    NATIVE_UUID,
    ApprovalAliasServer,
)


async def enter(tracker, entry, key):
    if entry == "approval":
        return await tracker.execution_approved(issue_key=key)
    if entry == "spec":
        return await tracker.read_fire_spec(issue_key=key)
    if entry == "family":
        return await tracker.read_criteria(issue_key=key)
    return await tracker.read_scope_labels(ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))


@pytest.mark.parametrize("entry", ["approval", "spec", "family", "labels"])
async def test_display_key_cannot_adopt_foreign_response_with_unrelated_uuid(entry):
    class Swapped(ApprovalAliasServer):
        def _tool_get_issue(self, arguments):
            payload = super()._tool_get_issue(arguments)
            if arguments["id"] == "ROOT-1":
                payload["id"] = "FOREIGN-1"
                payload["uuid"] = NATIVE_UUID
            return payload

    server = Swapped(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server)
    with pytest.raises((ScopeReadError, CriterionReadError)):
        await enter(tracker, entry, "ROOT-1")
    assert server.tool_calls("list_issues") == []
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("entry", ["approval", "spec", "labels"])
@pytest.mark.parametrize("field", ["labels", "parentId"])
async def test_alias_does_not_default_missing_approval_identity_fields(entry, field):
    class Missing(ApprovalAliasServer):
        def _tool_get_issue(self, arguments):
            payload = super()._tool_get_issue(arguments)
            if arguments["id"] == NATIVE_UUID:
                payload.pop(field)
            return payload

    server = Missing(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server)
    with pytest.raises(TrackerProtocolError):
        await enter(tracker, entry, NATIVE_UUID)
    assert server.tool_calls("list_issues") == []
    assert server.tool_calls("save_issue") == []


async def test_normalized_alias_cache_is_local_to_one_current_approval_read():
    server = ApprovalAliasServer(reported_uuid=NATIVE_UUID)
    tracker = tracker_over(server)
    assert await tracker.execution_approved(issue_key=NATIVE_UUID) is True
    server.issues["ROOT-1"].labels.remove(FIRE_SCOPE_LABEL)
    assert await tracker.execution_approved(issue_key=NATIVE_UUID) is False
    with pytest.raises(FireSpecEntryError, match="approval"):
        await tracker.read_fire_spec(issue_key=NATIVE_UUID)
    assert len(server.tool_calls("get_issue")) == 3
    assert server.tool_calls("list_issues") == []
    assert server.tool_calls("save_issue") == []
