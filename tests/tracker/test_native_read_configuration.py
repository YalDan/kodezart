"""Native classification reads consume validated owned configuration at boot."""

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import TrackerBootValidationError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.tracker_boot import (
    configured_mappings,
    owned_mappings,
    reconcile_tracker_mappings,
    validate_tracker_mappings,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import MappingKind, TrackerBackend
from tests.fakes import FakeLinearMcpServer

LABELS = {
    "criterion": "acceptance-condition",
    "decision": "recorded-question",
    "tracker": "execution-history",
}


def configured(server):
    operation = OperationConfig(
        operation_name="fixture", workspace="fixture", issue_labels=LABELS
    )
    tracker, _ = build_tracker(
        backend=TrackerBackend.LINEAR,
        retry=RetryPolicy(attempts=1, initial_delay=0),
        operation=operation,
        caller=server,
    )
    return operation, tracker


async def test_actual_boot_creates_owned_labels_and_reentry_writes_nothing():
    server = FakeLinearMcpServer()
    operation, tracker = configured(server)
    assert {ref.kind for ref in owned_mappings(operation)} == {MappingKind.ISSUE_LABEL}
    assert {ref.identifier for ref in configured_mappings(operation)} == set(
        LABELS.values()
    )
    first = await reconcile_tracker_mappings(tracker=tracker, config=operation)
    writes = list(server.tool_calls("create_issue_label"))
    assert {row["name"] for row in writes} == set(LABELS.values())
    assert len(writes) == len(LABELS)
    second = await reconcile_tracker_mappings(tracker=tracker, config=operation)
    assert server.tool_calls("create_issue_label") == writes
    assert first.config == second.config == operation
    assert operation_bindings(operation)["issue_labels"] == LABELS


async def test_validation_alone_does_not_silently_skip_missing_native_labels():
    server = FakeLinearMcpServer()
    operation, tracker = configured(server)
    with pytest.raises(TrackerBootValidationError):
        await validate_tracker_mappings(tracker=tracker, config=operation)
    assert server.tool_calls("create_issue_label") == []
