"""Live ready selection against both the shipped MCP adapter and domain port."""

import asyncio
from dataclasses import dataclass

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import TrackerProtocolError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import (
    CriterionReadError,
    EmptyFireCriteriaError,
    ScopedExecutionUnavailableError,
    ScopePlanRefusalError,
    ScopeReadError,
    ScopeSupersessionReadError,
)
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import OperationMemberAbsentError, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort
from tests.test_forge_origin_selection import (
    FORGE_ORIGIN,
    ForbiddenWorkflowEngine,
    _drive,
)
from tests.tracker.conftest import (
    QUEUE_STATE_LABELS,
    STATE_TYPES,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
)
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    OTHER_PROJECT,
    PROJECT,
    ScopeMcpIssue,
    ScopeMcpServer,
    _container,
)

LABELS = {
    "criterion": "acceptance-condition",
    "tracker": "execution-history",
    "decision": "recorded-question",
}
APPROVAL = {member.value: f"admission/{member.value}" for member in ScopeLabel}
READY_STATES = {**STATE_TYPES, "Triage": "triage"}


def row(key, *, parent=None, kind="unstarted", label=None, blockers=()):
    return ScopeMcpIssue(
        id=key,
        parent_id=parent,
        status=next(name for name, value in READY_STATES.items() if value == kind),
        status_type=kind,
        labels=[] if label is None else [LABELS[label]],
        relations=[("blockedBy", target) for target in blockers],
    )


def native_tracker(server, labels=LABELS):
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels=labels,
        scope_labels=APPROVAL,
        criteria_stage_label_key=None,
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1, initial_delay=1.0),
        ledger=SelfWriteLedger(),
    )


@dataclass
class ReadyFixture:
    tracker: TrackerPort
    fake: FakeTrackerPort
    server: ScopeMcpServer

    def state(self, key, kind):
        name = next(name for name, value in READY_STATES.items() if value == kind)
        self.server.issues[key].status = name
        self.server.issues[key].status_type = kind
        self.fake.issues[key] = self.fake.issues[key].model_copy(
            update={"state_name": name, "state_kind": WorkflowStateKind(kind)}
        )

    def approve(self, ref=PROJECT, *, approved=True):
        labels = [APPROVAL["approved"]] if approved else []
        if ref.kind is ScopeKind.ISSUE:
            issue = self.server.issues[ref.key]
            issue.labels = [
                value for value in issue.labels if value not in APPROVAL.values()
            ]
            issue.labels.extend(labels)
        elif ref.kind is ScopeKind.PROJECT:
            self.server.projects[ref.key]["labels"] = labels
        else:
            self.server.initiatives[ref.key]["labels"] = labels
        self.fake.scope_label_members[ref] = (
            frozenset({ScopeLabel.APPROVED}) if approved else frozenset()
        )

    def assert_read_only(self):
        assert not self.fake.issue_writes
        assert not self.fake.queue_writes
        assert not self.fake.comment_writes
        assert not self.fake.claim_writes
        assert {name for name, _ in self.server.calls} <= {
            "get_issue",
            "get_project",
            "get_initiative",
            "get_milestone",
            "list_issues",
            "list_projects",
            "list_milestones",
        }


@pytest.fixture(params=["linear", "fake"])
def ready_fixture(request):
    async def build(rows, *, approved=True):
        server = ScopeMcpServer()
        server.issues = {item.id: item for item in rows}
        server.state_types.update(READY_STATES)
        native = native_tracker(server)
        fake = FakeTrackerPort(
            issues=[await native.read_issue(issue_key=item.id) for item in rows],
            scope_containers=[
                _container(PROJECT, INITIATIVE),
                _container(INITIATIVE),
                _container(MILESTONE, PROJECT),
            ],
            scope_memberships={
                PROJECT: [item.id for item in rows if item.project_key == PROJECT.key],
                MILESTONE: [
                    item.id for item in rows if item.milestone_key == MILESTONE.key
                ],
            },
        )
        fixture = ReadyFixture(
            native if request.param == "linear" else fake, fake, server
        )
        fixture.approve(approved=approved)
        server.calls.clear()
        return fixture

    return build


def pair():
    return [
        row("blocker"),
        row("blocker-check", parent="blocker", label="criterion"),
        row("lane", blockers=("blocker",)),
        row("lane-check", parent="lane", label="criterion"),
    ]


def keys(selection):
    return [entry.issue.issue_key for entry in selection.ready]


async def test_blocker_subtree_recomputed_across_ticks_without_parent_state(
    ready_fixture,
):
    fixture = await ready_fixture(pair())
    fixture.state("blocker", "completed")
    first = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(first) == ["blocker"]
    assert [(item.issue_key, item.blocker_keys) for item in first.blocked] == [
        ("lane", ("blocker",))
    ]
    fixture.state("blocker-check", "completed")
    fixture.state("blocker", "unstarted")
    second = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(second) == ["lane"]
    assert second.blocked == ()
    assert [item.issue_key for item in second.ready[0].gap] == ["lane-check"]
    fixture.state("blocker-check", "started")
    assert keys(await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)) == [
        "blocker"
    ]
    fixture.assert_read_only()


async def test_deliverable_grandchildren_outside_membership_are_owed_and_dispatched(
    ready_fixture,
):
    """The same subtree read carries the grandchild and discharges the blocker.

    The blocker's own check is graded and the deliverable it parents sits
    outside the container filter with a check still open.  That check is what
    the blocker still owes, so it is dispatched to the blocker and holds the
    lane behind it; grading it releases both.
    """
    rows = pair()
    rows += [
        row("nested", parent="blocker"),
        row("deep-check", parent="nested", label="criterion"),
    ]
    for item in rows[-2:]:
        item.project_key = OTHER_PROJECT
        item.milestone_key = None
    fixture = await ready_fixture(rows)
    fixture.state("blocker-check", "completed")
    fixture.state("nested", "completed")
    first = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(first) == ["blocker"]
    assert [item.issue_key for item in first.ready[0].gap] == ["deep-check"]
    assert first.blocked[0].issue_key == "lane"
    fixture.state("deep-check", "completed")
    fixture.state("nested", "unstarted")
    assert keys(await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)) == [
        "lane"
    ]
    fixture.assert_read_only()


async def test_a_graded_lane_over_an_out_of_filter_open_child_is_not_at_rest(
    ready_fixture,
):
    """The shape the ruling of 2026-09-09 was settled on: no silent rest.

    Every criterion the lane itself carries is graded, and the deliverable it
    parents is out of the scope's container filter with a check still open.
    Under the abolished reading the scope reported nothing ready and nothing
    blocked while that check stayed open and unreachable forever.
    """
    rows = [
        row("lane"),
        row("lane-check", parent="lane", label="criterion", kind="completed"),
        row("child", parent="lane"),
        row("child-check", parent="child", label="criterion"),
    ]
    for item in rows[-2:]:
        item.project_key = OTHER_PROJECT
        item.milestone_key = None
    fixture = await ready_fixture(rows)
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(selection) == ["lane"]
    assert [item.issue_key for item in selection.ready[0].gap] == ["child-check"]
    assert selection.blocked == ()
    fixture.assert_read_only()


@pytest.mark.parametrize(
    "ref", [PROJECT, INITIATIVE, ScopeRef(kind=ScopeKind.ISSUE, key="blocker")]
)
async def test_approval_is_current_self_or_ancestor_not_parent_state(
    ready_fixture, ref
):
    fixture = await ready_fixture(pair(), approved=False)
    assert (await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)).ready == ()
    fixture.approve(ref)
    assert keys(await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)) == [
        "blocker"
    ]
    fixture.approve(ref, approved=False)
    assert (await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)).ready == ()
    fixture.assert_read_only()


@pytest.mark.parametrize("kind", ["canceled", "duplicate"])
async def test_unreadable_supersession_is_explicit_never_silent_closure(
    ready_fixture, kind
):
    fixture = await ready_fixture(pair())
    fixture.state("blocker-check", kind)
    with pytest.raises(ScopeSupersessionReadError) as caught:
        await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert caught.value.criterion_keys == ("blocker-check",)
    fixture.assert_read_only()


async def test_unapproved_canceled_lane_does_not_require_an_unused_reference(
    ready_fixture,
):
    fixture = await ready_fixture(pair(), approved=False)
    fixture.state("blocker-check", "canceled")
    assert (await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)).ready == ()


@pytest.mark.parametrize("label", ["tracker", "decision"])
async def test_record_issues_have_no_gap_and_never_become_candidates(
    ready_fixture, label
):
    fixture = await ready_fixture([row("record", label=label, kind="completed")])
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert selection.ready == selection.blocked == ()
    fixture.assert_read_only()


async def test_approved_deliverable_without_criteria_refuses(ready_fixture):
    fixture = await ready_fixture([row("lane", kind="completed")])
    with pytest.raises(EmptyFireCriteriaError, match="lane"):
        await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)


async def test_outside_scope_open_blocker_is_not_an_in_scope_ready_constraint(
    ready_fixture,
):
    rows = pair()
    rows[0].project_key = OTHER_PROJECT
    rows[1].project_key = OTHER_PROJECT
    fixture = await ready_fixture(rows)
    assert keys(await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)) == [
        "lane"
    ]


@pytest.mark.parametrize(
    "damage", ["approval", "body", "child-added", "child-removed", "parent"]
)
async def test_changed_facts_during_ready_read_refuse(
    ready_fixture, monkeypatch, damage
):
    fixture = await ready_fixture(pair())
    original = fixture.tracker.execution_approved
    changed = False

    async def approve(*, issue_key):
        nonlocal changed
        result = await original(issue_key=issue_key)
        if not changed:
            changed = True
            if damage == "approval":
                fixture.approve(approved=False)
            elif damage == "body":
                fixture.server.issues["lane-check"].description = "changed"
                fixture.fake.issues["lane-check"] = fixture.fake.issues[
                    "lane-check"
                ].model_copy(update={"body": "changed"})
            elif damage == "child-added":
                fixture.server.issues["new"] = row(
                    "new", parent="blocker", label="criterion"
                )
                fixture.fake.issues["new"] = fixture.fake.issues[
                    "blocker-check"
                ].model_copy(update={"issue_key": "new"})
            elif damage == "child-removed":
                del fixture.server.issues["blocker-check"]
                del fixture.fake.issues["blocker-check"]
            else:
                fixture.server.issues["blocker-check"].parent_id = "lane"
                fixture.fake.issues["blocker-check"] = fixture.fake.issues[
                    "blocker-check"
                ].model_copy(update={"parent_key": "lane"})
        return result

    monkeypatch.setattr(fixture.tracker, "execution_approved", approve)
    with pytest.raises(ScopeReadError):
        await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    fixture.assert_read_only()


@pytest.mark.parametrize("missing", list(LABELS))
async def test_missing_native_classification_mapping_refuses_before_read(missing):
    server = ScopeMcpServer()
    tracker = native_tracker(
        server, {key: value for key, value in LABELS.items() if key != missing}
    )
    with pytest.raises(OperationMemberAbsentError, match=missing):
        await read_scope_ready(ref=PROJECT, tracker=tracker)
    assert server.calls == []


@pytest.mark.parametrize("missing", ["labels", "relations"])
async def test_native_outside_descendant_omission_cannot_close_blocker(missing):
    rows = [
        *pair(),
        row("nested", parent="blocker"),
        row("deep", parent="nested", label="criterion"),
    ]
    rows[-2].project_key = rows[-1].project_key = OTHER_PROJECT
    server = ScopeMcpServer()
    server.issues = {item.id: item for item in rows}
    server.state_types.update(STATE_TYPES)
    tracker = native_tracker(server)
    original = tracker._call

    async def call(name, args):
        payload = await original(name, args)
        if name == "get_issue" and args["id"] == "deep":
            return {key: value for key, value in payload.items() if key != missing}
        return payload

    tracker._call = call
    with pytest.raises(
        CriterionReadError if missing == "labels" else TrackerProtocolError
    ):
        await read_scope_ready(ref=PROJECT, tracker=tracker)


async def test_unavailable_scoped_entry_does_not_read_readiness(
    ready_fixture, monkeypatch
):
    fixture = await ready_fixture(pair())
    original = fixture.tracker.execution_approved
    reads = []

    async def approve(*, issue_key):
        reads.append(issue_key)
        return await original(issue_key=issue_key)

    monkeypatch.setattr(fixture.tracker, "execution_approved", approve)
    engine = OriginRoutedWorkflowEngine(
        forge_arm=ForbiddenWorkflowEngine(),
        forge_less_arm=ForbiddenWorkflowEngine(),
    )
    with pytest.raises(ScopedExecutionUnavailableError, match="Scoped graph execution"):
        await _drive(engine, repo_url=FORGE_ORIGIN, scope=PROJECT)
    assert reads == []
    fixture.assert_read_only()


@pytest.mark.parametrize(
    "label,kind", [("decision", "unstarted"), ("criterion", "backlog")]
)
async def test_outside_filter_descendant_keeps_the_same_named_stage_barrier(
    ready_fixture, label, kind
):
    rows = pair()
    extra = row("outside", parent="blocker", label=label, kind=kind)
    extra.project_key = OTHER_PROJECT
    extra.milestone_key = None
    fixture = await ready_fixture([*rows, extra])
    fixture.state("blocker-check", "completed")
    with pytest.raises(ScopePlanRefusalError) as caught:
        await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert (
        caught.value.open_decisions
        if label == "decision"
        else caught.value.backlog_criteria
    ) == ("outside",)
    fixture.assert_read_only()


async def test_cancelled_approval_read_never_returns_selection_or_writes(
    ready_fixture, monkeypatch
):
    fixture = await ready_fixture(pair())
    entered = asyncio.Event()

    async def approve(*, issue_key):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(fixture.tracker, "execution_approved", approve)
    task = asyncio.create_task(read_scope_ready(ref=PROJECT, tracker=fixture.tracker))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fixture.assert_read_only()


@pytest.mark.parametrize("changed", ["outside-descendant", "new-scope-root"])
async def test_final_tree_and_scope_observations_each_detect_independent_movement(
    ready_fixture, monkeypatch, changed
):
    rows = [
        *pair(),
        row("nested", parent="blocker"),
        row("deep-check", parent="nested", label="criterion", kind="completed"),
    ]
    rows[-2].project_key = rows[-1].project_key = OTHER_PROJECT
    fixture = await ready_fixture(rows)
    fixture.state("blocker-check", "completed")
    original = fixture.tracker.execution_approved
    moved = False

    async def approve(*, issue_key):
        nonlocal moved
        result = await original(issue_key=issue_key)
        if not moved:
            moved = True
            if changed == "outside-descendant":
                # Neither the original project members nor their immediate
                # criterion children change. Only the full subtree sees it.
                fixture.state("deep-check", "unstarted")
            else:
                # Existing trees remain byte-identical; only a new root joins
                # the container. A tree-only final check cannot detect this.
                fixture.server.issues["new-root"] = row("new-root")
                fixture.server.issues["new-check"] = row(
                    "new-check", parent="new-root", label="criterion"
                )
                fixture.fake.issues["new-root"] = fixture.fake.issues[
                    "blocker"
                ].model_copy(update={"issue_key": "new-root"})
                fixture.fake.issues["new-check"] = fixture.fake.issues[
                    "lane-check"
                ].model_copy(
                    update={"issue_key": "new-check", "parent_key": "new-root"}
                )
                fixture.fake.scope_memberships[PROJECT] += ("new-root", "new-check")
        return result

    monkeypatch.setattr(fixture.tracker, "execution_approved", approve)
    with pytest.raises(ScopeReadError, match="changed during readiness"):
        await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    fixture.assert_read_only()


@pytest.mark.parametrize("label", ["tracker", "decision"])
async def test_closed_record_blocker_has_no_deliverable_gap(ready_fixture, label):
    fixture = await ready_fixture(
        [
            row("record", label=label, kind="completed"),
            row("lane", blockers=("record",)),
            row("lane-check", parent="lane", label="criterion"),
        ]
    )
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
    assert keys(selection) == ["lane"]
    assert selection.blocked == ()
    fixture.assert_read_only()
