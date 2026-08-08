"""The tracker fixture workspace, shared by every adapter under conformance.

The workspace is stated ONCE, in vendor shape, and every adapter's factory
is responsible for producing a ``TrackerPort`` that serves it.  A second
adapter joins the suite by adding one entry to ``TRACKER_ADAPTERS`` — no
test is copied, which is the whole point of a port-level suite.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import LifecycleStage
from tests.fakes import (
    FakeLinearMcpServer,
    FakeMcpAsset,
    FakeMcpHistoryEntry,
    FakeMcpIssue,
)

FIXTURE_NOW: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

APPROVER = "fixture-approver"
BYSTANDER = "fixture-bystander"

QUEUE_STATE_LABELS: dict[str, str] = {
    "triage": "queue:triage",
    "proposed": "queue:proposed",
    "approved": "queue:approved",
    "done": "queue:done",
    "decision": "queue:decision",
}
WORKFLOW_STATE_NAMES: dict[LifecycleStage, str] = {
    LifecycleStage.IN_PROGRESS: "In Progress",
    LifecycleStage.IN_REVIEW: "In Review",
    LifecycleStage.DONE: "Done",
}
TEAM_IDENTIFIERS: dict[str, str] = {"engineering": "fixture-team"}

STATE_TYPES: dict[str, str] = {
    "Backlog": "backlog",
    "Todo": "unstarted",
    "In Progress": "started",
    "In Review": "started",
    "Done": "completed",
    "Canceled": "canceled",
}

CLAIMED_ISSUE = "FIX-1"
APPROVED_ISSUE = "FIX-2"
ASSET_ISSUE = "FIX-3"

DOCUMENT_KEY = "doc-1"
DOCUMENT_CONTENT = "fixture document body"


def fixture_server() -> FakeLinearMcpServer:
    """A fresh fake workspace — one per test, never shared."""
    return FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(
                id=CLAIMED_ISSUE,
                title="claimable",
                description="an issue two passes race for",
                priority_raw=2,
                status="Todo",
                status_type="unstarted",
                labels=["queue:approved", "area:runtime"],
                created_at=FIXTURE_NOW - timedelta(days=3),
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=APPROVED_ISSUE,
                title="approved with a blocker",
                description="body",
                priority_raw=1,
                status="Backlog",
                status_type="backlog",
                labels=["queue:approved"],
                relations=[("blockedBy", CLAIMED_ISSUE), ("child", ASSET_ISSUE)],
                parent_id="FIX-0",
                assignee=BYSTANDER,
                created_at=FIXTURE_NOW - timedelta(days=1),
                updated_at=FIXTURE_NOW,
            ),
            FakeMcpIssue(
                id=ASSET_ISSUE,
                title="carries assets",
                priority_raw=0,
                status="Done",
                status_type="completed",
                labels=["queue:done"],
                attachments=[
                    FakeMcpAsset(
                        id="asset-1",
                        title="spec.pdf",
                        url="https://tracker.invalid/asset-1",
                        content_type="application/pdf",
                        size=1024,
                    ),
                ],
                documents=[
                    FakeMcpAsset(
                        id=DOCUMENT_KEY,
                        title="checkpoint",
                        url="https://tracker.invalid/doc-1",
                    ),
                ],
                created_at=FIXTURE_NOW - timedelta(days=10),
                updated_at=FIXTURE_NOW,
            ),
        ],
        documents={DOCUMENT_KEY: DOCUMENT_CONTENT},
        history={
            APPROVED_ISSUE: [
                FakeMcpHistoryEntry(
                    actor=BYSTANDER,
                    created_at=FIXTURE_NOW - timedelta(days=2),
                    added_labels=["queue:proposed"],
                ),
                FakeMcpHistoryEntry(
                    actor=APPROVER,
                    created_at=FIXTURE_NOW - timedelta(days=1),
                    added_labels=["queue:approved"],
                    removed_labels=["queue:proposed"],
                ),
            ],
        },
        users=[APPROVER, BYSTANDER],
        teams=["fixture-team"],
        labels=list(QUEUE_STATE_LABELS.values()),
        statuses=list(STATE_TYPES),
        state_types=STATE_TYPES,
        actor=APPROVER,
    )


def linear_over_fake_mcp(server: FakeLinearMcpServer) -> TrackerPort:
    """The shipped Linear adapter, dialing the in-process fake MCP server."""
    return LinearMcpTracker(
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        max_retries=0,
        retry_backoff_factor=1.0,
        clock=lambda: FIXTURE_NOW,
    )


TRACKER_ADAPTERS: dict[str, Callable[[FakeLinearMcpServer], TrackerPort]] = {
    "linear-mcp": linear_over_fake_mcp,
}


@pytest.fixture
def server() -> FakeLinearMcpServer:
    """A fresh fixture workspace."""
    return fixture_server()


@pytest.fixture(params=sorted(TRACKER_ADAPTERS))
def tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
) -> TrackerPort:
    """Every registered adapter, over the same fixture workspace."""
    factory = TRACKER_ADAPTERS[request.param]
    return factory(server)
