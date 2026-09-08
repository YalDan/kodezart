"""Native scope facts stop the actual scoped entry before any fire dispatch."""

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import (
    CriterionReadError,
    ScopeCycleError,
    ScopePlanRefusalError,
    ScopeReadError,
)
from kodezart.services.scope_planning import read_scope_plan
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeTrackerPort, make_tracker_issue
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
from tests.tracker.test_scope_reads import MILESTONE, ScopeMcpIssue, ScopeMcpServer

SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key="root")
LABELS = {
    "criterion": "acceptance-condition",
    "decision": "recorded-question",
    "tracker": "execution-history",
}


def row(key, *, parent=None, kind="unstarted", label=None, blockers=()):
    state = next(name for name, value in STATE_TYPES.items() if value == kind)
    return ScopeMcpIssue(
        id=key,
        parent_id=parent,
        status=state,
        status_type=kind,
        labels=[] if label is None else [LABELS[label]],
        relations=[("blockedBy", key) for key in blockers],
    )


def native_tracker(server, labels):
    return LinearMcpTracker(
        marker_prefixes=MARKER_PREFIXES,
        issue_labels=labels,
        scope_labels={},
        criteria_stage_label_key=None,
        caller=server,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        max_retries=0,
        retry_backoff_factor=1.0,
        ledger=SelfWriteLedger(),
    )


@pytest.fixture(params=["linear", "fake"])
def build(request):
    def factory(rows, *, scope=SCOPE, membership=()):
        server = ScopeMcpServer()
        server.issues = {item.id: item for item in rows}
        server.state_types.update(STATE_TYPES)
        if request.param == "linear":
            tracker = native_tracker(server, LABELS)
        else:
            tracker = FakeTrackerPort(
                scope_containers=[]
                if scope == SCOPE
                else [
                    ScopeContainer(
                        ref=scope,
                        name="fixture",
                        description="",
                        url="https://tracker.invalid/scope",
                    )
                ],
                scope_memberships={} if scope == SCOPE else {scope: list(membership)},
                issues=[
                    make_tracker_issue(
                        item.id,
                        parent_key=item.parent_id,
                        state_name=item.status,
                        state_kind=WorkflowStateKind(item.status_type),
                        blocked_by=[
                            key for kind, key in item.relations if kind == "blockedBy"
                        ],
                    ).model_copy(
                        update={
                            "issue_labels": frozenset(
                                key
                                for key, spelling in LABELS.items()
                                if spelling in item.labels
                            )
                        }
                    )
                    for item in rows
                ],
            )
        return tracker

    return factory


@pytest.mark.parametrize("barrier", ["decision", "backlog", "crossing", "cycle"])
async def test_native_stage_barriers_name_keys_before_actual_engine_dispatch(
    build, barrier
):
    rows = [row("root"), row("criterion", parent="root", label="criterion")]
    if barrier == "decision":
        rows.append(row("question", parent="root", label="decision"))
        offending, error = "question", ScopePlanRefusalError
    elif barrier == "backlog":
        rows[1] = row("criterion", parent="root", label="criterion", kind="backlog")
        offending, error = "criterion", ScopePlanRefusalError
    elif barrier == "crossing":
        rows[1] = row(
            "criterion", parent="root", label="criterion", blockers=("outside",)
        )
        rows.append(row("outside", parent="other-root", label="criterion"))
        offending, error = "outside", ScopePlanRefusalError
    else:
        rows[0] = row("root", blockers=("external",))
        rows.append(row("external", blockers=("root",)))
        offending, error = "external", ScopeCycleError
    tracker = build(rows)
    engine = OriginRoutedWorkflowEngine(
        forge_arm=ForbiddenWorkflowEngine(),
        forge_less_arm=ForbiddenWorkflowEngine(),
        tracker=tracker,
        tracker_preparer=None,
    )
    with pytest.raises(error, match=offending):
        await _drive(engine, repo_url=FORGE_ORIGIN, scope=SCOPE)


@pytest.mark.parametrize("decision_kind", ["completed", "canceled", "duplicate"])
async def test_closed_decisions_and_sibling_edges_return_native_plan_facts(
    build, decision_kind
):
    tracker = build(
        [
            row("root", blockers=("external",)),
            row("first", parent="root", label="criterion"),
            row("second", parent="root", label="criterion", blockers=("first",)),
            row("question", parent="root", label="decision", kind=decision_kind),
            row("external", blockers=("ancestor",)),
            row("ancestor", kind="completed"),
        ]
    )
    plan = await read_scope_plan(ref=SCOPE, tracker=tracker)
    assert {issue.issue_key for issue in plan.scope.issues} == {
        "root",
        "first",
        "second",
        "question",
    }
    assert {issue.issue_key for issue in plan.dependencies} == {"external", "ancestor"}


@pytest.mark.parametrize(
    "damage",
    ["foreign-dependency", "changed-dependency", "changed-member", "new-member"],
)
async def test_unstable_native_facts_never_return_a_plan(build, monkeypatch, damage):
    tracker = build(
        [
            row("root", blockers=("external",)),
            row("external"),
            row("new", parent="root"),
        ]
    )
    original_read, original_scope = tracker.read_planning_issue, tracker.scope_issues
    reads = []

    async def read(*, issue_key):
        value = await original_read(issue_key=issue_key)
        if issue_key == "external":
            reads.append(issue_key)
            if damage == "foreign-dependency":
                return value.model_copy(update={"issue_key": "foreign"})
            if damage == "changed-dependency" and len(reads) > 1:
                return value.model_copy(update={"body": "changed"})
        return value

    calls = []

    async def scoped(*, ref):
        values = list(await original_scope(ref=ref))
        calls.append(ref)
        if damage == "new-member" and len(calls) == 1:
            values = [value for value in values if value.issue_key != "new"]
        if len(calls) > 1:
            if damage == "changed-member":
                values[0] = values[0].model_copy(update={"body": "changed"})
        return values

    monkeypatch.setattr(tracker, "read_planning_issue", read)
    monkeypatch.setattr(tracker, "scope_issues", scoped)
    with pytest.raises(ScopeReadError):
        await read_scope_plan(ref=SCOPE, tracker=tracker)


@pytest.mark.parametrize("missing", ["criterion", "decision"])
async def test_missing_native_label_mapping_refuses_before_any_scope_read(missing):
    server = ScopeMcpServer()
    tracker = native_tracker(
        server, {key: value for key, value in LABELS.items() if key != missing}
    )
    with pytest.raises(OperationMemberAbsentError, match=missing):
        await read_scope_plan(ref=SCOPE, tracker=tracker)
    assert server.calls == []


async def test_plan_keeps_criterion_children_outside_milestone_filter(build):
    parent = row("root")
    child = row("criterion", parent="root", label="criterion", kind="backlog")
    child.milestone_key = None
    tracker = build([parent, child], scope=MILESTONE, membership=["root"])
    with pytest.raises(ScopePlanRefusalError) as caught:
        await read_scope_plan(ref=MILESTONE, tracker=tracker)
    assert caught.value.backlog_criteria == ("criterion",)


async def test_every_barrier_kind_is_named_together(build):
    tracker = build(
        [
            row("root"),
            row("question", parent="root", label="decision"),
            row(
                "criterion",
                parent="root",
                label="criterion",
                kind="backlog",
                blockers=("outside",),
            ),
            row("outside", parent="elsewhere", label="criterion"),
        ]
    )
    with pytest.raises(ScopePlanRefusalError) as caught:
        await read_scope_plan(ref=SCOPE, tracker=tracker)
    assert caught.value.open_decisions == ("question",)
    assert caught.value.backlog_criteria == ("criterion",)
    assert caught.value.cross_subtree_edges == (("criterion", "outside"),)


@pytest.mark.parametrize("field", ["labels", "relations"])
@pytest.mark.parametrize("affected", ["root", "external", "criterion"])
async def test_native_planning_requires_reported_labels_and_relations(field, affected):
    class Omitted(ScopeMcpServer):
        def _tool_get_issue(self, arguments):
            value = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == affected:
                value.pop(field, None)
            return value

    server = Omitted()
    server.state_types.update(STATE_TYPES)
    server.issues = {
        "root": row("root", blockers=("external",)),
        "external": row("external"),
        "criterion": row("criterion", parent="root", label="criterion"),
    }
    tracker = native_tracker(server, LABELS)
    with pytest.raises((TrackerProtocolError, ScopeReadError, CriterionReadError)):
        await read_scope_plan(ref=SCOPE, tracker=tracker)


@pytest.mark.parametrize("damage", ["null-relations", "missing-arm"])
async def test_native_planning_requires_all_requested_relation_arms(damage):
    class Damaged(ScopeMcpServer):
        def _tool_get_issue(self, arguments):
            value = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == "root":
                if damage == "null-relations":
                    value["relations"] = None
                else:
                    value["relations"] = dict(value["relations"])
                    value["relations"].pop("blockedBy")
            return value

    server = Damaged()
    server.state_types.update(STATE_TYPES)
    server.issues = {"root": row("root")}
    with pytest.raises((TrackerProtocolError, ScopeReadError)):
        await read_scope_plan(ref=SCOPE, tracker=native_tracker(server, LABELS))
