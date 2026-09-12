"""The addressed native stream validates the concrete payloads it publishes."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.agent import (
    AssistantTextEvent,
    NodeSessionStartedEvent,
    SystemEvent,
)
from kodezart.types.domain.native_delivery import LaneDeliveryEvent, SkippedLaneDelivery
from kodezart.types.domain.node_session import NodeInvocation
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_runtime import ScopeLaneEvent
from tests.fakes import FIXTURE_EPOCH


@pytest.mark.parametrize(
    "inner",
    [
        AssistantTextEvent(text="evidence", model="model"),
        SystemEvent(subtype="init", data={"session_id": "actual-session"}),
        NodeSessionStartedEvent(
            invocation=NodeInvocation(
                run=RunIdentity(
                    kind=RunKind.FIRE, name="lane", started_at=FIXTURE_EPOCH
                ),
                node_key="evaluate",
                invocation_key="attempt-1",
                declared_sessions=1,
            ),
            session_id="actual-session",
        ),
        LaneDeliveryEvent(
            delivery=SkippedLaneDelivery(
                outcome=WorkflowOutcome.review_passed_no_pr_adapter,
                reason="No forge adapter for the selected origin",
            )
        ),
    ],
)
def test_scope_lane_round_trips_its_concrete_event(inner):
    event = ScopeLaneEvent(lane_key="lane", event=inner)
    parsed = ScopeLaneEvent.model_validate_json(event.model_dump_json())
    assert parsed == event
    assert type(parsed.event) is type(inner)


@pytest.mark.parametrize(
    "inner",
    [
        {"type": "unknown_progress"},
        {"type": "unknown_progress", "text": "not registered"},
        {"type": "assistant_text", "model": "missing text"},
        {"type": "assistant_text", "model": "model", "text": "evidence", "extra": True},
        {"type": "workflow_complete"},
        {"type": "lane_delivery", "delivery": {"phase": "pending"}},
    ],
)
def test_scope_lane_refuses_unknown_malformed_and_terminal_payloads(inner):
    with pytest.raises(ValidationError):
        ScopeLaneEvent.model_validate({"laneKey": "lane", "event": inner})


def test_queued_egress_preserves_authored_shape_and_required_native_nulls():
    from kodezart.handlers.agent_handler import _queued_event_payload
    from kodezart.types.domain.agent import (
        AuthoredWorkflowCompleteEvent,
        WorkflowScopeBaseEvent,
    )

    authored = AuthoredWorkflowCompleteEvent(
        feature_branch="feature",
        ralph_branch="iteration",
        total_iterations=1,
        accepted=True,
        outcome=WorkflowOutcome.review_passed_no_pr_adapter,
    )
    assert _queued_event_payload(authored) == authored.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert "prUrl" not in _queued_event_payload(authored)
    addressed = ScopeLaneEvent(
        lane_key="lane",
        event=WorkflowScopeBaseEvent(
            base_branch="main",
            base_role=None,
            inputs=[],
        ),
    )
    payload = _queued_event_payload(addressed)
    assert payload["event"]["baseRole"] is None
    assert ScopeLaneEvent.model_validate(payload) == addressed
