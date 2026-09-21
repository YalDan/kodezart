"""A complete native workspace and its domain double for model conformance."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.domain.model_surfaces import MODEL_CLASSIFICATION
from kodezart.types.domain.dispatch import SelfWriteLedger
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeLinearMcpServer,
    FakeMcpIssue,
    FakeTrackerPort,
)
from tests.tracker.conftest import (
    MARKER_PREFIXES,
    QUEUE_STATE_LABELS,
    STATE_TYPES,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
)

#: One name for the model, taken from the layer that owns it: the
#: workspace a case builds and the job that resolves a lease have to
#: name the same model or a case proves nothing about it.
CLASSIFICATION = MODEL_CLASSIFICATION
NATIVE_MARKER = "model:criterion-lifecycle"
CRITERION_MARKER = "acceptance-condition"


class ModelServer(FakeLinearMcpServer):
    """Exercise complete identity pages, including a non-final empty page."""

    def _tool_list_issues(self, arguments):
        selected = [
            issue
            for issue in self.issues.values()
            if (arguments.get("label") is None or arguments["label"] in issue.labels)
            and (
                arguments.get("parentId") is None
                or arguments["parentId"] == issue.parent_id
            )
        ]
        offset = int(arguments.get("cursor", "0"))
        limit = min(2, int(arguments.get("limit", 2)))
        end = offset + limit
        return {
            "issues": [{"id": issue.id} for issue in selected[offset:end]],
            "hasNextPage": end < len(selected),
            "cursor": str(end) if end < len(selected) else None,
        }


@dataclass
class ModelWorkspace:
    tracker: TrackerPort
    native: LinearMcpTracker
    fake: FakeTrackerPort
    server: ModelServer

    async def put(self, issue: FakeMcpIssue):
        self.server.issues[issue.id] = issue
        self.fake.issues[issue.id] = await self.native.read_planning_issue(
            issue_key=issue.id
        )

    async def seed(self, documents):
        for document in documents:
            await self.put(
                FakeMcpIssue(
                    id=document["key"],
                    description=document["body"],
                    parent_id=document.get("parent"),
                    labels=[
                        NATIVE_MARKER if label == CLASSIFICATION else CRITERION_MARKER
                        for label in document.get("labels", [])
                    ],
                )
            )

    def read_only(self):
        assert not self.fake.issue_writes
        assert not self.fake.comment_writes
        assert not self.fake.queue_writes
        assert not self.fake.claim_writes
        assert {name for name, _ in self.server.calls} <= {"get_issue", "list_issues"}


def _fixture_now() -> datetime:
    """The one instant both arms read.

    Stated rather than left to a wall clock: the fake workspace stamps its
    comment log at the fixture epoch, and an arm reading real time beside
    it would find every marker it had just written already expired.
    """
    return FIXTURE_EPOCH


async def model_workspace(
    adapter: str, *, clock: Callable[[], datetime] = _fixture_now
) -> ModelWorkspace:
    server = ModelServer(state_types=STATE_TYPES)
    native = LinearMcpTracker(
        clock=clock,
        caller=server,
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={CLASSIFICATION: NATIVE_MARKER, "criterion": CRITERION_MARKER},
        criteria_stage_label_key=None,
        ledger=SelfWriteLedger(),
        scope_labels={},
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1, initial_delay=1),
    )
    fake = FakeTrackerPort(clock=clock)
    return ModelWorkspace(native if adapter == "native" else fake, native, fake, server)
