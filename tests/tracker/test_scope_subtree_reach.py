"""A lane's state over a descendant its scope's container filter cannot reach.

One board shape, three readings, both implementations.  A scope member has
every one of its OWN criteria Done and parents a deliverable that sits in
another project; that deliverable still holds a criterion of its own.  What
the lane owes is its whole subtree, so the container filter changes what the
run can ADDRESS and never what the lane OWES.

The readings are the descendant open, the descendant graded, and the
descendant cancelled with no supersession on record.  Each runs through the
shipped Linear adapter over the in-process MCP server AND through the
in-process double, so neither implementation can answer "nothing left"
alone.
"""

from collections.abc import Mapping, Sequence

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.chains.scope_walker import (
    UnreachableCriterion,
    read_scope_ready,
    unreachable_criteria,
)
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeSupersessionReadError
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
#: The child criterion's state in each of the three readings, in the vendor's
#: own pair of spellings.
CHILD_STATES: dict[str, tuple[str, str]] = {
    "open": ("Todo", "unstarted"),
    "graded": ("Done", "completed"),
    "cancelled": ("Canceled", "canceled"),
}


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
            state_types=dict(CHILD_STATES.values()),
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


def _issues(*, child_state: str, out_of_filter: bool) -> list[ScopeMcpIssue]:
    """The one board shape every reading and both implementations run on."""
    child_project = OTHER_PROJECT if out_of_filter else PROJECT.key
    child_status, child_kind = CHILD_STATES[child_state]
    graded_status, graded_kind = CHILD_STATES["graded"]
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
            description="the lane's own criterion, already graded",
            labels=[CRITERION_LABEL],
            parent_id=LANE,
            status=graded_status,
            status_type=graded_kind,
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


@pytest.fixture(params=["linear-mcp", "fake-port"])
def implementation(request: pytest.FixtureRequest) -> str:
    """Both tracker implementations, over one stated board shape."""
    param: str = request.param
    return param


def board(implementation: str, *, child_state: str, out_of_filter: bool) -> TrackerPort:
    issues = _issues(child_state=child_state, out_of_filter=out_of_filter)
    if implementation == "linear-mcp":
        return _adapter(ReachMcpServer(issues=issues))
    return _double(issues)


async def test_the_lane_is_not_at_rest_while_the_hidden_descendant_is_open(
    implementation: str,
) -> None:
    """The lane's state follows the subtree, not the filtered member set.

    Its own criterion family is entirely graded, so a reading taken over
    the filtered members alone would report an empty gap and the scope
    would go to rest with this criterion open and unaddressable forever.
    """
    tracker = board(implementation, child_state="open", out_of_filter=True)

    own = await tracker.read_criteria(issue_key=LANE)
    assert [criterion.issue_key for criterion in own] == [LANE_CHECK]
    assert all(criterion.state_kind is WorkflowStateKind.COMPLETED for criterion in own)

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert {issue.issue_key for issue in ready.scope.issues} == {LANE, LANE_CHECK}
    assert [lane.issue.issue_key for lane in ready.ready] == [LANE]
    assert [criterion.issue_key for criterion in ready.ready[0].gap] == [CHILD_CHECK]
    assert ready.blocked == ()


async def test_the_same_lane_reads_at_rest_once_that_descendant_is_done(
    implementation: str,
) -> None:
    """Nothing else changed: the one open criterion moved to Done."""
    tracker = board(implementation, child_state="graded", out_of_filter=True)

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert {issue.issue_key for issue in ready.scope.issues} == {LANE, LANE_CHECK}
    assert ready.ready == ()
    assert ready.blocked == ()


async def test_a_cancelled_hidden_descendant_refuses_instead_of_reading_at_rest(
    implementation: str,
) -> None:
    """The graded-away arm the filtered reading cannot tell from silence.

    The lane owes nothing OPEN in either arithmetic here, so at-rest alone
    cannot separate them.  A cancellation with no supersession on record is
    not a closure, and the subtree read says so by name.  A reading taken
    over the filtered members never sees this criterion at all and reports
    the same restful nothing it reports for a graded one.
    """
    tracker = board(implementation, child_state="cancelled", out_of_filter=True)

    with pytest.raises(ScopeSupersessionReadError) as caught:
        await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert caught.value.criterion_keys == (CHILD_CHECK,)
    assert caught.value.ref == PROJECT


async def test_the_identical_shape_inside_the_filter_carries_the_child_as_a_member(
    implementation: str,
) -> None:
    """The control arm: the filter reaches the child, which walks in its own right."""
    tracker = board(implementation, child_state="open", out_of_filter=False)

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert {issue.issue_key for issue in ready.scope.issues} == {
        LANE,
        LANE_CHECK,
        CHILD,
        CHILD_CHECK,
    }
    assert {lane.issue.issue_key for lane in ready.ready} == {LANE, CHILD}
    assert ready.blocked == ()


def _named(ready) -> tuple[UnreachableCriterion, ...]:
    """What the read declares out of its own filter's reach, over its members."""
    return unreachable_criteria(
        ref=PROJECT,
        members={issue.issue_key for issue in ready.scope.issues},
        lanes=ready.ready,
    )


async def test_the_lane_owes_the_criterion_its_scope_cannot_reach(
    implementation: str,
) -> None:
    """The out-of-filter arm: the criterion is named with its key and reason."""
    tracker = board(implementation, child_state="open", out_of_filter=True)

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert _named(ready) == (
        UnreachableCriterion(
            issue_key=CHILD_CHECK, lane_key=LANE, reason=OTHER_PROJECT
        ),
    )


async def test_the_same_shape_inside_the_filter_names_no_unreachable_descendant(
    implementation: str,
) -> None:
    """The in-filter arm: the family carries the criterion, so nothing is named."""
    tracker = board(implementation, child_state="open", out_of_filter=False)

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert _named(ready) == ()
