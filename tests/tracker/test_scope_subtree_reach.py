"""A lane's state over a descendant its scope's container filter cannot reach.

One board shape, three readings, both implementations.  A scope member has
every one of its OWN criteria Done and parents a deliverable that sits in
another project; that deliverable still holds a criterion of its own.  What
the lane owes is its whole subtree, so the container filter changes what the
run can ADDRESS and never what the lane OWES.

The readings are the descendant open, the descendant graded, and the
descendant cancelled with no supersession on record.  The read names what its
filter cannot reach under a project and under a milestone reference alike.
Each runs through the shipped Linear adapter over the in-process MCP server
AND through the in-process double, so neither implementation can answer
"nothing left" alone.
"""

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.chains import scope_walker
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.backoff import RetryPolicy
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeReadError, ScopeSupersessionReadError
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.handlers.agent_handler import _queued_event_payload
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import LifecycleStage, ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import (
    UnreachableCriterion,
    UnreachableReason,
)
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    IssueRelationKind,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeLinearMcpServer, FakeTrackerPort, make_tracker_issue
from tests.integration import test_scope_runtime as walk
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_scope_reads import ScopeMcpIssue

PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="reach-project")
OTHER_PROJECT = "reach-other-project"
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="reach-milestone")
OTHER_MILESTONE = "reach-other-milestone"

LANE = "REACH-1"
LANE_CHECK = "REACH-2"
CHILD = "REACH-3"
CHILD_CHECK = "REACH-4"
BLOCKER = "REACH-5"
BLOCKER_CHECK = "REACH-6"

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


def _milestone(key: str) -> dict[str, object]:
    return {
        "id": key,
        "name": key,
        "description": f"Complete description of {key}",
        "progress": 0,
        "sortOrder": 0,
    }


class ReachMcpServer(FakeLinearMcpServer):
    """The project- and milestone-filtered issue reads this shape is measured through.

    Both milestones belong to the addressed project, because that is the
    shape a milestone reference is resolved through: the adapter walks the
    workspace's projects, lists each one's milestones and then reads the one
    it matched.
    """

    def __init__(self, *, issues: Sequence[ScopeMcpIssue]) -> None:
        super().__init__(
            issues=issues,
            projects={key: _project(key) for key in (PROJECT.key, OTHER_PROJECT)},
            state_types=dict(CHILD_STATES.values()),
            milestones={
                PROJECT.key: [_milestone(MILESTONE.key), _milestone(OTHER_MILESTONE)]
            },
        )

    def _tool_list_projects(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        return {"projects": list(self.projects.values()), "hasNextPage": False}

    def _tool_get_milestone(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        query = arguments["query"]
        for milestone in self.milestones[str(arguments["project"])]:
            if query in (milestone["id"], milestone["name"]):
                return milestone
        raise LookupError(f"No fixture milestone {query!r}")

    def _tool_list_issues(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        rows = [issue.entry() for issue in self.issues.values()]
        if "project" in arguments:
            rows = [row for row in rows if row["projectId"] == arguments["project"]]
        if "parentId" in arguments:
            rows = [row for row in rows if row["parentId"] == arguments["parentId"]]
        return {"issues": rows, "hasNextPage": False}


def _issues(
    *,
    child_state: str,
    out_of_filter: bool,
    ref: ScopeRef = PROJECT,
    child_milestone: str | None = OTHER_MILESTONE,
    approved: bool = True,
    blocked: bool = False,
) -> list[ScopeMcpIssue]:
    """The one board shape every reading and both implementations run on.

    Under a project reference the child leaves the filter by sitting in
    another project and no row carries a milestone at all.  Under a milestone
    reference every row sits in the addressed project — a milestone member
    with no owning project is not a shape the backend has — and the child
    leaves the filter by carrying *child_milestone*, which is another
    milestone or no milestone at all.  With *approved* off the lane carries
    no approval label of its own.  With *blocked* on, an in-filter member
    nobody approved, with an open criterion of its own, blocks the lane.
    """
    by_milestone = ref.kind is ScopeKind.MILESTONE
    lane_milestone = MILESTONE.key if by_milestone else None
    if not by_milestone:
        child_project = OTHER_PROJECT if out_of_filter else PROJECT.key
        child_milestone_key = None
    else:
        child_project = PROJECT.key
        child_milestone_key = child_milestone if out_of_filter else MILESTONE.key
    child_status, child_kind = CHILD_STATES[child_state]
    graded_status, graded_kind = CHILD_STATES["graded"]
    open_status, open_kind = CHILD_STATES["open"]
    blocker_rows = [
        ScopeMcpIssue(
            id=BLOCKER,
            description="an unapproved member the lane is blocked by",
            status="Todo",
            status_type="unstarted",
            project_key=PROJECT.key,
            milestone_key=lane_milestone,
        ),
        ScopeMcpIssue(
            id=BLOCKER_CHECK,
            description="the blocker's own criterion, still open",
            labels=[CRITERION_LABEL],
            parent_id=BLOCKER,
            status=open_status,
            status_type=open_kind,
            project_key=PROJECT.key,
            milestone_key=lane_milestone,
        ),
    ]
    return [
        ScopeMcpIssue(
            id=LANE,
            description="the lane the walk selects",
            labels=[APPROVED_LABEL] if approved else [],
            relations=[("blockedBy", BLOCKER)] if blocked else [],
            status="Todo",
            status_type="unstarted",
            project_key=PROJECT.key,
            milestone_key=lane_milestone,
        ),
        ScopeMcpIssue(
            id=LANE_CHECK,
            description="the lane's own criterion, already graded",
            labels=[CRITERION_LABEL],
            parent_id=LANE,
            status=graded_status,
            status_type=graded_kind,
            project_key=PROJECT.key,
            milestone_key=lane_milestone,
        ),
        ScopeMcpIssue(
            id=CHILD,
            description="the deliverable the lane parents",
            parent_id=LANE,
            status="Todo",
            status_type="unstarted",
            project_key=child_project,
            milestone_key=child_milestone_key,
        ),
        ScopeMcpIssue(
            id=CHILD_CHECK,
            description="the criterion the deliverable holds",
            labels=[CRITERION_LABEL],
            parent_id=CHILD,
            status=child_status,
            status_type=child_kind,
            project_key=child_project,
            milestone_key=child_milestone_key,
        ),
        *(blocker_rows if blocked else []),
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
        relations=tuple(
            IssueRelation(kind=IssueRelationKind.BLOCKED_BY, issue_key=key)
            for kind, key in issue.relations
            if kind == "blockedBy"
        ),
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/issue/{issue.id}",
    )


def _double(
    issues: Sequence[ScopeMcpIssue], *, approved: bool = True, blocked: bool = False
) -> FakeTrackerPort:
    """The same board as a domain double, under either container filter.

    Each filter selects the rows it carries: the project's members are the
    rows in that project, the milestone's are the rows on that milestone.
    The approval stays on the project, which is the only container level a
    label lives at — a milestone adds none of its own.  With *approved* off
    no approval is seeded at all.  With *blocked* on the approval sits on
    the lane itself, as the adapter's board carries it, because a project
    approval would also approve the blocker the board leaves unapproved.
    """
    approval = ScopeRef(kind=ScopeKind.ISSUE, key=LANE) if blocked else PROJECT
    return FakeTrackerPort(
        issues=[_domain_issue(issue) for issue in issues],
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name=PROJECT.key,
                description="",
                url=f"https://tracker.invalid/project/{PROJECT.key}",
            ),
            ScopeContainer(
                ref=MILESTONE,
                name=MILESTONE.key,
                description="",
                url=None,
            ),
        ],
        scope_memberships={
            PROJECT: [issue.id for issue in issues if issue.project_key == PROJECT.key],
            MILESTONE: [
                issue.id for issue in issues if issue.milestone_key == MILESTONE.key
            ],
        },
        scope_label_members=(
            {approval: frozenset({ScopeLabel.APPROVED})} if approved else {}
        ),
    )


@pytest.fixture(params=["linear-mcp", "fake-port"])
def implementation(request: pytest.FixtureRequest) -> str:
    """Both tracker implementations, over one stated board shape."""
    param: str = request.param
    return param


def board(
    implementation: str,
    *,
    child_state: str,
    out_of_filter: bool,
    ref: ScopeRef = PROJECT,
    child_milestone: str | None = OTHER_MILESTONE,
    approved: bool = True,
    blocked: bool = False,
) -> TrackerPort:
    issues = _issues(
        child_state=child_state,
        out_of_filter=out_of_filter,
        ref=ref,
        child_milestone=child_milestone,
        approved=approved,
        blocked=blocked,
    )
    if implementation == "linear-mcp":
        return _adapter(ReachMcpServer(issues=issues))
    return _double(issues, approved=approved, blocked=blocked)


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
    assert ready.unreachable == ()


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


@pytest.mark.parametrize(
    ("ref", "child_milestone", "named"),
    [
        (
            PROJECT,
            None,
            UnreachableCriterion(
                issue_key=CHILD_CHECK,
                reason=UnreachableReason.OTHER_PROJECT,
                container=OTHER_PROJECT,
            ),
        ),
        (
            MILESTONE,
            OTHER_MILESTONE,
            UnreachableCriterion(
                issue_key=CHILD_CHECK,
                reason=UnreachableReason.OTHER_MILESTONE,
                container=OTHER_MILESTONE,
            ),
        ),
        (
            MILESTONE,
            None,
            UnreachableCriterion(
                issue_key=CHILD_CHECK,
                reason=UnreachableReason.NO_MILESTONE,
                container=None,
            ),
        ),
    ],
    ids=["other-project", "other-milestone", "no-milestone"],
)
async def test_the_lane_owes_the_criterion_its_scope_cannot_reach(
    implementation: str,
    ref: ScopeRef,
    child_milestone: str | None,
    named: UnreachableCriterion,
) -> None:
    """The out-of-filter arm: the read itself names the key and the reason.

    Three placements of the one criterion, each stated in the terms the
    addressed filter is itself stated in: in another project under a project
    reference, and under another milestone or under none at all under a
    milestone reference.
    """
    tracker = board(
        implementation,
        child_state="open",
        out_of_filter=True,
        ref=ref,
        child_milestone=child_milestone,
    )

    ready = await read_scope_ready(ref=ref, tracker=tracker)

    assert ready.unreachable == (named,)
    assert [lane.issue.issue_key for lane in ready.ready] == [LANE]
    assert ready.blocked == ()
    assert CHILD_CHECK in ready.unresolved


@pytest.mark.parametrize("ref", [PROJECT, MILESTONE], ids=["project", "milestone"])
async def test_the_same_shape_inside_the_filter_names_no_unreachable_descendant(
    implementation: str, ref: ScopeRef
) -> None:
    """The in-filter arm: the family carries the criterion, so nothing is named."""
    tracker = board(implementation, child_state="open", out_of_filter=False, ref=ref)

    ready = await read_scope_ready(ref=ref, tracker=tracker)

    assert ready.unreachable == ()
    assert {lane.issue.issue_key for lane in ready.ready} == {LANE, CHILD}


async def test_the_read_names_the_unreachable_criterion_of_a_lane_nobody_approved(
    implementation: str,
) -> None:
    """The naming covers every member, not only the lanes the walk can fire.

    The out-of-filter shape with the lane's approval withheld: nothing is
    ready, yet the criterion the filter cannot reach is still named, because
    an obligation under a member nobody approved is one the scope has not
    discharged either.
    """
    tracker = board(
        implementation, child_state="open", out_of_filter=True, approved=False
    )

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert ready.ready == ()
    assert ready.unapproved == (LANE,)
    assert CHILD_CHECK in ready.unresolved
    assert ready.unreachable == (
        UnreachableCriterion(
            issue_key=CHILD_CHECK,
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
    )


async def test_the_read_names_the_unreachable_criterion_of_a_blocked_lane(
    implementation: str,
) -> None:
    """A lane the walk cannot fire still has its unreachable criterion named.

    The out-of-filter shape with the lane blocked by an in-filter member
    whose own criterion is open: nothing is ready and the lane is blocked,
    yet the criterion its filter cannot reach is named with its reason and
    container, because being blocked defers the obligation and discharges
    none of it.
    """
    tracker = board(
        implementation, child_state="open", out_of_filter=True, blocked=True
    )

    ready = await read_scope_ready(ref=PROJECT, tracker=tracker)

    assert ready.ready == ()
    assert LANE in {entry.issue_key for entry in ready.blocked}
    assert ready.unreachable == (
        UnreachableCriterion(
            issue_key=CHILD_CHECK,
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
    )


# ---------------------------------------------------------------------------
# The two private seams the read's naming is computed by, asked directly.
# ---------------------------------------------------------------------------


def _criterion(key: str, *, project: str | None, milestone: str | None) -> TrackerIssue:
    """One criterion record carrying both container fields, set independently."""
    return _domain_issue(
        ScopeMcpIssue(
            id=key,
            description="a criterion outside some filter",
            labels=[CRITERION_LABEL],
            project_key=project,
            milestone_key=milestone,
        )
    )


#: One criterion per filter kind, and per answer within a kind: the container
#: field the filter reads is set and absent in turn.  Every row carries BOTH
#: container fields wherever both can be set, to values that differ, so a
#: reading that folded one filter's field into the other's would answer with
#: the wrong container instead of passing on a row where the two agree.
FILTER_ROWS: tuple[
    tuple[ScopeKind, str | None, str | None, UnreachableCriterion], ...
] = (
    (
        ScopeKind.PROJECT,
        OTHER_PROJECT,
        OTHER_MILESTONE,
        UnreachableCriterion(
            issue_key=CHILD_CHECK,
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
    ),
    (
        ScopeKind.PROJECT,
        None,
        OTHER_MILESTONE,
        UnreachableCriterion(
            issue_key=CHILD_CHECK, reason=UnreachableReason.NO_PROJECT
        ),
    ),
    (
        ScopeKind.INITIATIVE,
        OTHER_PROJECT,
        OTHER_MILESTONE,
        UnreachableCriterion(
            issue_key=CHILD_CHECK,
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
    ),
    (
        ScopeKind.MILESTONE,
        OTHER_PROJECT,
        OTHER_MILESTONE,
        UnreachableCriterion(
            issue_key=CHILD_CHECK,
            reason=UnreachableReason.OTHER_MILESTONE,
            container=OTHER_MILESTONE,
        ),
    ),
    (
        ScopeKind.MILESTONE,
        OTHER_PROJECT,
        None,
        UnreachableCriterion(
            issue_key=CHILD_CHECK, reason=UnreachableReason.NO_MILESTONE
        ),
    ),
)


def test_every_filter_reason_is_stated_in_its_filters_own_terms() -> None:
    """Each kind reads the field its own filter reads, and never the other.

    Every reason the vocabulary holds is produced by one of these rows, so a
    member added to it without a filter that answers with it fails here rather
    than shipping unreachable.
    """
    produced = []
    for kind, project, milestone, named in FILTER_ROWS:
        entry = scope_walker._filter_reason(
            _criterion(CHILD_CHECK, project=project, milestone=milestone),
            ref=ScopeRef(kind=kind, key="whatever-this-filter-addresses"),
        )
        assert entry == named, kind
        produced.append(entry)

    assert {entry.reason for entry in produced} == set(UnreachableReason)
    # The spellings the walk's event carries on the wire, as documented.
    assert {reason.value for reason in UnreachableReason} == {
        "other_project",
        "no_project",
        "other_milestone",
        "no_milestone",
    }


def test_an_issue_scope_that_omits_an_open_criterion_refuses() -> None:
    """An issue scope's members ARE its subtree, so the omission is a bad read."""
    with pytest.raises(ScopeReadError):
        scope_walker._filter_reason(
            _criterion(CHILD_CHECK, project=OTHER_PROJECT, milestone=OTHER_MILESTONE),
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=LANE),
        )


def test_the_unreachable_criteria_are_the_unresolved_ones_the_members_omit() -> None:
    """The naming is the open reading projected onto membership and nothing else.

    The closure carries open criteria under an approved member, under an
    unapproved one and under a blocked one, plus one closed criterion outside
    the filter and one open criterion the filter does carry. What comes back is
    every open criterion the members omit, whatever held the member up: an
    obligation under a member nobody approved is one the scope has not
    discharged either.
    """
    members = {"APPROVED", "UNAPPROVED", "BLOCKED", "APPROVED-CHECK"}
    facts = {
        "APPROVED": make_tracker_issue("APPROVED", project_id=PROJECT.key),
        "APPROVED-CHECK": make_tracker_issue(
            "APPROVED-CHECK",
            parent_key="APPROVED",
            project_id=PROJECT.key,
            issue_labels=frozenset({"criterion"}),
        ),
        "OUT-OF-APPROVED": make_tracker_issue(
            "OUT-OF-APPROVED", parent_key="APPROVED", project_id=OTHER_PROJECT
        ),
        "OUT-OF-APPROVED-CHECK": make_tracker_issue(
            "OUT-OF-APPROVED-CHECK",
            parent_key="OUT-OF-APPROVED",
            project_id=OTHER_PROJECT,
            issue_labels=frozenset({"criterion"}),
        ),
        "OUT-OF-APPROVED-GRADED": make_tracker_issue(
            "OUT-OF-APPROVED-GRADED",
            parent_key="OUT-OF-APPROVED",
            project_id=OTHER_PROJECT,
            state_name="Done",
            state_kind=WorkflowStateKind.COMPLETED,
            issue_labels=frozenset({"criterion"}),
        ),
        "UNAPPROVED": make_tracker_issue("UNAPPROVED", project_id=PROJECT.key),
        "OUT-OF-UNAPPROVED": make_tracker_issue(
            "OUT-OF-UNAPPROVED", parent_key="UNAPPROVED", project_id=OTHER_PROJECT
        ),
        "OUT-OF-UNAPPROVED-CHECK": make_tracker_issue(
            "OUT-OF-UNAPPROVED-CHECK",
            parent_key="OUT-OF-UNAPPROVED",
            project_id=OTHER_PROJECT,
            issue_labels=frozenset({"criterion"}),
        ),
        "BLOCKED": make_tracker_issue(
            "BLOCKED", project_id=PROJECT.key, blocked_by=["APPROVED"]
        ),
        "OUT-OF-BLOCKED": make_tracker_issue(
            "OUT-OF-BLOCKED", parent_key="BLOCKED", project_id=None
        ),
        "OUT-OF-BLOCKED-CHECK": make_tracker_issue(
            "OUT-OF-BLOCKED-CHECK",
            parent_key="OUT-OF-BLOCKED",
            project_id=None,
            issue_labels=frozenset({"criterion"}),
        ),
    }
    closure = SubtreeClosure(facts=facts, ref=PROJECT)

    produced = scope_walker._unreachable_criteria(closure=closure, members=members)

    assert [entry.issue_key for entry in produced] == [
        key for key in closure.open_criterion_keys() if key not in members
    ]
    assert produced == (
        UnreachableCriterion(
            issue_key="OUT-OF-APPROVED-CHECK",
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
        UnreachableCriterion(
            issue_key="OUT-OF-UNAPPROVED-CHECK",
            reason=UnreachableReason.OTHER_PROJECT,
            container=OTHER_PROJECT,
        ),
        UnreachableCriterion(
            issue_key="OUT-OF-BLOCKED-CHECK", reason=UnreachableReason.NO_PROJECT
        ),
    )


# ---------------------------------------------------------------------------
# The wired walk carries the naming on every observation it emits (KOD-875).
# ---------------------------------------------------------------------------


def _deliverable_rows(port: FakeTrackerPort, *, project: str | None) -> None:
    """A deliverable under lane A, outside the walk board's own filter or in it.

    It carries the lane's own run-stage markers, so the subtree read the walk
    takes of the lane passes the named stage barriers, and one open criterion
    of its own, which is the obligation the lane owes and the scope cannot
    address.
    """
    lane = port.issues["A"]
    port.issues["A-deliverable"] = make_tracker_issue(
        "A-deliverable",
        parent_key="A",
        project_id=project,
        issue_labels=lane.issue_labels,
        body="the deliverable lane A parents\n",
    )
    port.issues["A-deliverable/check"] = make_tracker_issue(
        "A-deliverable/check",
        parent_key="A-deliverable",
        project_id=project,
        issue_labels=frozenset({"criterion"}),
        body="**Check:** A-deliverable/check live Check  bytes\n**Evidence:** —",
    )


@pytest.mark.parametrize(
    ("placement", "project", "named", "wire"),
    [
        (
            "other-project",
            "reach-elsewhere",
            (
                UnreachableCriterion(
                    issue_key="A-deliverable/check",
                    reason=UnreachableReason.OTHER_PROJECT,
                    container="reach-elsewhere",
                ),
            ),
            [
                {
                    "issueKey": "A-deliverable/check",
                    "reason": "other_project",
                    "container": "reach-elsewhere",
                }
            ],
        ),
        (
            "no-project",
            None,
            (
                UnreachableCriterion(
                    issue_key="A-deliverable/check",
                    reason=UnreachableReason.NO_PROJECT,
                ),
            ),
            [{"issueKey": "A-deliverable/check", "reason": "no_project"}],
        ),
        ("inside", "reach-elsewhere", (), []),
    ],
)
async def test_the_walk_observes_the_criterion_its_filter_cannot_reach(
    placement: str,
    project: str | None,
    named: tuple[UnreachableCriterion, ...],
    wire: list[dict[str, str]],
) -> None:
    """The composed walk copies the read's naming onto the event it emits.

    The same board three ways: the deliverable in another project, in no
    project, and carried by the scope's own membership. The two out-of-filter
    placements are named on the observation and reach a consumer through the
    shipped egress under the reason the filter answered with; the in-filter one
    names nothing and walks the child as a lane of its own.
    """
    port = walk.board(lanes=("A",))
    # The lane's own criterion is graded, so what it still owes is the
    # deliverable's criterion and nothing else.
    port.issues["A/check"] = port.issues["A/check"].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )
    _deliverable_rows(port, project=project)
    if placement == "inside":
        port.scope_memberships[walk.SCOPE] = ("A", "A-deliverable")

    # An out-of-filter placement is read on to the tick after lane A's fire,
    # so the naming is seen carried past the walk's first observation.
    wanted = 1 if placement == "inside" else 2
    stream = walk.drive(walk.runtime(port=port))
    observed: list[ScopeWalkEvent] = []
    async with asyncio.timeout(walk.WALK_BOUND_SECONDS):
        async for emitted in stream:
            if isinstance(emitted, ScopeWalkEvent):
                observed.append(emitted)
                if len(observed) == wanted:
                    break
    await stream.aclose()
    assert len(observed) == wanted, "a walk that stopped early states nothing here"
    event = observed[0]
    observation = event.observation

    assert observation.tick == 1
    # The deliverable took the lane's approval, so the naming is the one
    # reading that separates the placements.
    assert observation.unapproved_lanes == ()
    assert observation.unreachable_criteria == named
    payload = _queued_event_payload(event)
    assert payload["observation"]["unreachableCriteria"] == wire
    assert ScopeWalkEvent.model_validate(payload) == event
    if placement == "inside":
        assert "A-deliverable" in observation.ready
    else:
        assert observation.ready == ("A",)
        # The deliverable's criterion is neither a member nor on A's own
        # evaluation set, so A's fire leaves it open and the next tick names
        # it again.
        after_the_fire = observed[1].observation
        assert after_the_fire.tick == 2
        assert after_the_fire.dispatched == ("A",)
        assert after_the_fire.unreachable_criteria == named
