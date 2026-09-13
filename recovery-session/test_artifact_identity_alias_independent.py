"""The identity port validates actual native responses without losing aliases."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from tests.tracker.test_artifact_split_identity_independent import (
    CHILD,
    ORIGINAL,
    NativeSplitServer,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over

NATIVE_UUID = "00000000-0000-4000-8000-000000000073"


class IdentityAliasServer(NativeSplitServer):
    def __init__(self, reported_uuid):
        super().__init__()
        self.reported_uuid = reported_uuid

    def _tool_get_issue(self, arguments):
        payload = dict(super()._tool_get_issue({**arguments, "id": CHILD}))
        if self.reported_uuid is not None:
            payload["uuid"] = self.reported_uuid
        return payload


async def test_identity_lookup_preserves_an_attested_uuid_in_one_native_read():
    server = IdentityAliasServer(NATIVE_UUID)
    identity = await tracker_over(server).read_issue_identity(issue_key=NATIVE_UUID)
    assert identity == ORIGINAL
    assert server.tool_calls("get_issue") == [
        {"id": NATIVE_UUID, "includeRelations": True}
    ]


@pytest.mark.parametrize(
    "reported_uuid",
    [None, "00000000-0000-4000-8000-000000000074", "not-a-uuid"],
)
async def test_identity_lookup_refuses_an_unattested_or_malformed_alias(reported_uuid):
    server = IdentityAliasServer(reported_uuid)
    with pytest.raises(TrackerProtocolError):
        await tracker_over(server).read_issue_identity(issue_key=NATIVE_UUID)
    assert len(server.tool_calls("get_issue")) == 1
    assert server.tool_calls("save_issue") == []
