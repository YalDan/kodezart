"""Independent native artifact controls beyond the extraction's direct tests."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import CriterionReadError, WriteBackReadError
from kodezart.services.tracker_artifacts import read_tracker_artifact
from tests.chains.test_native_artifact_readback import KINDS, native


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("missing", ["status", "statusType"])
async def test_native_state_omission_cannot_be_a_complete_artifact(kind, missing):
    tracker, server, surface = native(kind, missing=missing)
    with pytest.raises((TrackerProtocolError, CriterionReadError)):
        await read_tracker_artifact(tracker=tracker, surface=surface)
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("kind", KINDS)
async def test_substituted_native_identity_refuses_before_artifact(kind):
    tracker, server, surface = native(kind)
    original = server._tool_get_issue

    def foreign(arguments):
        payload = dict(original(arguments))
        if arguments["id"] == "CHECK-1":
            payload["id"] = "OTHER-NATIVE-KEY"
        return payload

    server._tool_get_issue = foreign
    with pytest.raises((CriterionReadError, WriteBackReadError)):
        await read_tracker_artifact(tracker=tracker, surface=surface)
    assert server.tool_calls("save_issue") == []
