"""The tracker fixture workspace, shared by every adapter under conformance.

The workspace is stated ONCE, in vendor shape, and every adapter's factory
is responsible for producing a ``TrackerPort`` that serves it.  A second
adapter joins the suite by adding one entry to ``TRACKER_ADAPTERS`` — no
test is copied, which is the whole point of a port-level suite.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import IssueQuery, StateTransition
from tests.fakes import (
    FakeLinearMcpServer,
    FakeMcpAsset,
    FakeMcpHistoryEntry,
    FakeMcpIssue,
    FakeTrackerPort,
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
PAGE_SIZE = 50


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


async def _snapshot(source: TrackerPort) -> FakeTrackerPort:
    """Read the fixture workspace through the adapter into domain objects."""
    keys = [
        issue.issue_key
        for issue in await source.scan_issues(query=IssueQuery(page_size=PAGE_SIZE))
    ]
    issues = [await source.read_issue(issue_key=key) for key in keys]
    provenance: dict[tuple[str, QueueState], StateTransition] = {}
    for key in keys:
        for state in QueueState:
            transition = await source.queue_state_provenance(
                issue_key=key,
                state=state,
            )
            if transition is not None:
                provenance[(key, state)] = transition
    return FakeTrackerPort(
        issues=issues,
        provenance=provenance,
        assets={key: await source.list_issue_assets(issue_key=key) for key in keys},
        documents={
            DOCUMENT_KEY: await source.read_document(document_key=DOCUMENT_KEY),
        },
        recorded_work_refs={key: await source.work_refs(issue_key=key) for key in keys},
        known_identifiers=[
            *(APPROVER, BYSTANDER),
            *TEAM_IDENTIFIERS.values(),
            *QUEUE_STATE_LABELS.values(),
            *WORKFLOW_STATE_NAMES.values(),
        ],
        clock=lambda: FIXTURE_NOW,
    )


def fake_port_over_fixture(server: FakeLinearMcpServer) -> TrackerPort:
    """The consumer double, seeded from the SAME fixture workspace.

    Seeded by reading through the adapter rather than by restating the
    workspace in domain vocabulary: a hand-written second statement of the
    fixture is a place for the two to disagree, and the disagreement would
    show up as the double being wrong about the thing consumers trust it
    for.  ``asyncio.run`` is safe here because the workspace is pure
    in-memory state with nothing bound to a loop.
    """
    return asyncio.run(_snapshot(linear_over_fake_mcp(server)))


#: Real adapters — every one must serve the fixture workspace unchanged.
TRACKER_ADAPTERS: dict[str, Callable[[FakeLinearMcpServer], TrackerPort]] = {
    "linear-mcp": linear_over_fake_mcp,
}

#: Test doubles that consumers are tested on.  They run the SAME suite, per
#: the ruling that this is what keeps them honest: a double that drifts from
#: the contract fails exactly where a non-conforming vendor adapter would.
TRACKER_DOUBLES: dict[str, Callable[[FakeLinearMcpServer], TrackerPort]] = {
    "fake-port": fake_port_over_fixture,
}

TRACKER_IMPLEMENTATIONS: dict[str, Callable[[FakeLinearMcpServer], TrackerPort]] = {
    **TRACKER_ADAPTERS,
    **TRACKER_DOUBLES,
}


@pytest.fixture
def server() -> FakeLinearMcpServer:
    """A fresh fixture workspace."""
    return fixture_server()


@pytest.fixture(params=sorted(TRACKER_IMPLEMENTATIONS))
def tracker(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
) -> TrackerPort:
    """Every registered adapter AND double, over one fixture workspace."""
    factory = TRACKER_IMPLEMENTATIONS[request.param]
    return factory(server)


@pytest.fixture(params=sorted(TRACKER_ADAPTERS))
def adapter(
    request: pytest.FixtureRequest,
    server: FakeLinearMcpServer,
) -> TrackerPort:
    """Registered ADAPTERS only — for rules about backend substitutability."""
    factory = TRACKER_ADAPTERS[request.param]
    return factory(server)
