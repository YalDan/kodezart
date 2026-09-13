import pytest
from kodezart.domain.errors import SurfaceLeaseError
from tests.tracker.conftest import CLAIMED_ISSUE, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over

async def test_production_adapter_has_a_creation_capability_that_refuses_unowned_writes():
    server = fixture_server()
    tracker = tracker_over(server)
    with pytest.raises(SurfaceLeaseError):
        await tracker.create_criterion_if_absent(parent_key=CLAIMED_ISSUE, title="Check bytes", check="Input bytes are retained.", do="Compare the input bytes.", holder="actual-run-id")
    assert not [args for name, args in server.calls if name == "save_issue"]
