from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import TrackerBackend
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue


async def test_actual_configured_constructor_reads_current_native_criterion():
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        issue_labels={"criterion": "Configured Check"},
    )
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(id="parent"),
            FakeMcpIssue(id="child", parent_id="parent", labels=["Configured Check"]),
        ]
    )
    tracker, _ = build_tracker(
        backend=TrackerBackend.LINEAR,
        retry=RetryPolicy(attempts=1, initial_delay=0),
        operation=operation,
        caller=server,
    )
    children = await tracker.read_criteria(issue_key="parent")
    assert [child.issue_key for child in children] == ["child"]
    assert children[0].issue_labels == frozenset({"criterion"})
    assert not server.tool_calls("save_issue")


async def test_actual_empty_current_family_is_a_successful_read():
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        issue_labels={"criterion": "Configured Check"},
    )
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id="parent")])
    tracker, _ = build_tracker(
        backend=TrackerBackend.LINEAR,
        retry=RetryPolicy(attempts=1, initial_delay=0),
        operation=operation,
        caller=server,
    )
    assert tuple(await tracker.read_criteria(issue_key="parent")) == ()
