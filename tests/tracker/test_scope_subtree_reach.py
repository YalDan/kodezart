"""An open criterion under a lane that the scope's container filter misses.

One board shape, two arms, both implementations. A scope member has every
one of its OWN criteria Done and parents a deliverable that sits in another
project; that deliverable still holds an open criterion. The lane owes it —
what an issue owes is its whole subtree — and the scope cannot address it
on its own, because the filter the walk was given does not carry it.

The pair runs through the shipped Linear adapter over the in-process MCP
server AND through the in-process double, so neither implementation can
answer "nothing left" alone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.domain.scope_reach import unreachable_criteria
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeLinearMcpServer, FakeTrackerPort
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_scope_reads import ScopeMcpIssue

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="reach-project")
OTHER_PROJECT = "reach-other-project"

LANE = "REACH-1"
LANE_CHECK = "REACH-2"
CHILD = "REACH-3"
CHILD_CHECK = "REACH-4"

CRITERION_LABEL = "acceptance-condition"
APPROVED_LABEL = "execution-consent"
QUEUE_STATE_LABELS = {"approved": "queue:approved"}
WORKFLOW_STATE_NAMES = {
    LifecycleStage.IN_PROGRESS: "In Progress",
    LifecycleStage.IN_REVIEW: "In Review",
    LifecycleStage.DONE: "Done",
}
DONE = ("Done", "completed")
TODO = ("Todo", "unstarted")


def _project(key: str) -> dict[str, object]:
    return {
        "id": key,
        "name": key,
        "description": f"Complete description of {key}",
        "url": f"https://tracker.invalid/project/{key}",
        "initiatives": [],
        "labels": [],
    }


class ReachMcpServer(FakeLinearMcpServer):
    """The project-filtered issue reads this shape is measured through."""

    def __init__(self, *, issues: Sequence[ScopeMcpIssue]) -> None:
        super().__init__(
            issues=issues,
            projects={key: _project(key) for key in (PROJECT.key, OTHER_PROJECT)},
        )

    def _tool_list_issues(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        rows = [issue.entry() for issue in self.issues.values()]
        if "project" in arguments:
            rows = [row for row in rows if row["projectId"] == arguments["project"]]
        if "parentId" in arguments:
            rows = [row for row in rows if row["parentId"] == arguments["parentId"]]
        return {"issues": rows, "hasNextPage": False}


def _issues(*, child_done: bool, child_project: str) -> list[ScopeMcpIssue]:
    """The one board shape both arms and both implementations are read from."""
    child_status, child_kind = DONE if child_done else TODO
    return [
        ScopeMcpIssue(
            id=LANE,
            description="the lane the walk selects",
            labels=[APPROVED_LABEL],
            status="Todo",
            status_type="unstarted",
            project_key=PROJECT.key,
            milestone_key=None,
        ),
        ScopeMcpIssue(
            id=LANE_CHECK,
            description="the lane's own criterion, already Done",
            labels=[CRITERION_LABEL],
            parent_id=LANE,
            status=DONE[0],
            status_type=DONE[1],
            project_key=PROJECT.key,
            milestone_key=None,
        ),
        ScopeMcpIssue(
            id=CHILD,
            description="the deliverable the lane parents",
            parent_id=LANE,
            status="Todo",
            status_type="unstarted",
            project_key=child_project,
            milestone_key=None,
        ),
        ScopeMcpIssue(
            id=CHILD_CHECK,
            description="the criterion the deliverable holds",
            labels=[CRITERION_LABEL],
            parent_id=CHILD,
            status=child_status,
            status_type=child_kind,
            project_key=child_project,
            milestone_key=None,
        ),
    ]


def _adapter(server: FakeLinearMcpServer) -> TrackerPort:
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels={
            "criterion": CRITERION_LABEL,
            "decision": "decision-record",
            "tracker": "tracker-record",
        },
        criteria_stage_label_key=None,
        scope_labels={ScopeLabel.APPROVED.value: APPROVED_LABEL},
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers={},
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        clock=lambda: FIXTURE_NOW,
        ledger=SelfWriteLedger(),
    )


def _domain_issue(issue: ScopeMcpIssue) -> TrackerIssue:
    return TrackerIssue(
        issue_key=issue.id,
        title=issue.title,
        body=issue.description,
        priority=IssuePriority.NONE,
        state_name=issue.status,
        state_kind=WorkflowStateKind(issue.status_type),
        queue_states=frozenset(),
        issue_labels=frozenset(
            "criterion" if label == CRITERION_LABEL else label for label in issue.labels
        ),
        team_key=None,
        project=issue.project_key,
        project_id=issue.project_key,
        milestone_key=issue.milestone_key,
        parent_key=issue.parent_id,
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/issue/{issue.id}",
    )


def _double(issues: Sequence[ScopeMcpIssue]) -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[_domain_issue(issue) for issue in issues],
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name=PROJECT.key,
                description="",
                url=f"https://tracker.invalid/project/{PROJECT.key}",
            ),
        ],
        scope_memberships={
            PROJECT: [issue.id for issue in issues if issue.project_key == PROJECT.key],
        },
        scope_label_members={PROJECT: frozenset({ScopeLabel.APPROVED})},
    )


@dataclass(frozen=True)
class Board:
    tracker: TrackerPort
    name: str


@pytest.fixture(params=["linear-mcp", "fake-port"])
def implementation(request: pytest.FixtureRequest) -> str:
    """Both tracker implementations, over one stated board shape."""
    param: str = request.param
    return param


def board(implementation: str, *, child_done: bool, out_of_filter: bool) -> Board:
    issues = _issues(
        child_done=child_done,
        child_project=OTHER_PROJECT if out_of_filter else PROJECT.key,
    )
    tracker = (
        _adapter(ReachMcpServer(issues=issues))
        if implementation == "linear-mcp"
        else _double(issues)
    )
    return Board(tracker=tracker, name=implementation)


async def test_the_lane_owes_the_criterion_its_scope_cannot_reach(
    implementation: str,
) -> None:
    """The out-of-filter arm: the read names the descendant and its reason."""
    reading = board(implementation, child_done=False, out_of_filter=True)

    ready = await read_scope_ready(ref=PROJECT, tracker=reading.tracker)

    members = {issue.issue_key for issue in ready.scope.issues}
    assert members == {LANE, LANE_CHECK}
    assert [lane.issue.issue_key for lane in ready.ready] == [LANE]
    assert [criterion.issue_key for criterion in ready.ready[0].gap] == [CHILD_CHECK]
    unreachable = unreachable_criteria(
        ref=PROJECT,
        members={issue.issue_key: issue for issue in ready.scope.issues},
        lanes=ready.ready,
    )
    assert len(unreachable) == 1
    named = unreachable[0]
    assert named.issue_key == CHILD_CHECK
    assert named.lane_key == LANE
    assert named.filter_kind is ScopeKind.PROJECT
    assert named.filter_key == PROJECT.key
    assert named.container_key == OTHER_PROJECT


async def test_the_same_shape_inside_the_filter_names_no_unreachable_descendant(
    implementation: str,
) -> None:
    """The in-filter arm: the child is a member and is dispatched as before."""
    reading = board(implementation, child_done=False, out_of_filter=False)

    ready = await read_scope_ready(ref=PROJECT, tracker=reading.tracker)

    assert {issue.issue_key for issue in ready.scope.issues} == {
        LANE,
        LANE_CHECK,
        CHILD,
        CHILD_CHECK,
    }
    assert {lane.issue.issue_key for lane in ready.ready} == {LANE, CHILD}
    assert (
        unreachable_criteria(
            ref=PROJECT,
            members={issue.issue_key: issue for issue in ready.scope.issues},
            lanes=ready.ready,
        )
        == ()
    )


async def test_the_lane_is_not_at_rest_while_the_hidden_descendant_is_open(
    implementation: str,
) -> None:
    """The lane's state follows the subtree, not the filtered member set.

    Its own criterion family is entirely Done, so a reading taken over the
    filtered members would report an empty gap and the scope would go to
    rest with this criterion open forever.
    """
    reading = board(implementation, child_done=False, out_of_filter=True)

    own = await reading.tracker.read_criteria(issue_key=LANE)
    assert [criterion.issue_key for criterion in own] == [LANE_CHECK]
    assert all(criterion.state_kind is WorkflowStateKind.COMPLETED for criterion in own)

    ready = await read_scope_ready(ref=PROJECT, tracker=reading.tracker)

    # Not at rest: the scope still reports the lane as work, and the named
    # reason is exactly the descendant its own filter cannot reach.
    assert ready.ready != ()
    assert [lane.issue.issue_key for lane in ready.ready] == [LANE]
    assert [criterion.issue_key for criterion in ready.ready[0].gap] == [CHILD_CHECK]
    unreachable = unreachable_criteria(
        ref=PROJECT,
        members={issue.issue_key: issue for issue in ready.scope.issues},
        lanes=ready.ready,
    )
    assert [named.issue_key for named in unreachable] == [CHILD_CHECK]


async def test_the_same_lane_reads_at_rest_once_that_descendant_is_done(
    implementation: str,
) -> None:
    """Nothing else changed: the one open criterion moved to Done."""
    reading = board(implementation, child_done=True, out_of_filter=True)

    ready = await read_scope_ready(ref=PROJECT, tracker=reading.tracker)

    assert {issue.issue_key for issue in ready.scope.issues} == {LANE, LANE_CHECK}
    assert ready.ready == ()
    assert ready.blocked == ()
    assert (
        unreachable_criteria(
            ref=PROJECT,
            members={issue.issue_key: issue for issue in ready.scope.issues},
            lanes=ready.ready,
        )
        == ()
    )
