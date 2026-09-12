"""Native classification reads consume validated owned configuration at boot."""

import asyncio

import pytest
from pydantic import ValidationError

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import TrackerBootValidationError, TrackerProtocolError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import CriterionReadError
from kodezart.services.tracker_boot import (
    configured_mappings,
    owned_mappings,
    reconcile_tracker_mappings,
    validate_tracker_mappings,
)
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.tracker import MappingKind, TrackerBackend, TrackerIssue
from tests.fakes import (
    FakeLinearMcpServer,
    FakeMcpIssue,
    FakeTrackerPort,
    make_tracker_issue,
)
from tests.tracker.native_read_fixtures import LABELS, tracker_over


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


@pytest.mark.parametrize(
    "labels",
    [
        {"criterion": ""},
        {"": "label"},
        {"criterion": "  "},
        {"criterion": "same", "decision": "same"},
    ],
)
def test_invalid_or_ambiguous_label_mapping_refuses_at_configuration(labels):
    with pytest.raises(ValidationError, match="issue_labels"):
        OperationConfig(
            operation_name="fixture", workspace="fixture", issue_labels=labels
        )


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


@pytest.mark.parametrize("missing", ["labels", "relations"])
async def test_strict_planning_read_rejects_omitted_native_facts(missing):
    class MissingFacts(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            payload.pop(missing)
            return payload

    server = MissingFacts(issues=[FakeMcpIssue(id="root")])
    tracker = tracker_over(server)
    with pytest.raises(TrackerProtocolError):
        await tracker.read_planning_issue(issue_key="root")
    assert server.tool_calls("save_issue") == []


@pytest.mark.parametrize("missing", ["criterion", "decision"])
def test_scope_read_capability_refuses_without_io(missing):
    server = FakeLinearMcpServer()
    tracker = tracker_over(
        server,
        issue_labels={key: value for key, value in LABELS.items() if key != missing},
    )
    with pytest.raises(OperationMemberAbsentError, match=missing):
        tracker.require_scope_plan_reads()
    assert server.calls == []


@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
async def test_programming_errors_and_cancellation_escape_native_family_read(failure):
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id="root")])
    tracker = tracker_over(server)
    original = server.call_tool
    raised = failure("external boundary failure")

    async def broken(*, name, arguments):
        if name == "list_issues":
            raise raised
        return await original(name=name, arguments=arguments)

    server.call_tool = broken
    with pytest.raises(failure) as caught:
        await tracker.read_criteria(issue_key="root")
    assert caught.value is raised
    assert server.tool_calls("save_issue") == []


async def test_fake_contract_retains_current_native_identity_and_empty_distinction():
    parent = make_tracker_issue("root")
    child = TrackerIssue.model_validate(
        {
            **make_tracker_issue("native-child", parent_key="root").model_dump(),
            "issue_labels": ["criterion"],
        }
    )
    fake = FakeTrackerPort(issues=[parent, child])
    assert tuple(await fake.read_criteria(issue_key="root")) == (child,)
    assert tuple(await fake.read_criteria(issue_key="native-child")) == ()
    fake.issues[child.issue_key] = TrackerIssue.model_validate(
        {**child.model_dump(), "issue_labels": []}
    )
    assert tuple(await fake.read_criteria(issue_key="root")) == ()
    with pytest.raises(CriterionReadError):
        await fake.read_criteria(issue_key="missing")
