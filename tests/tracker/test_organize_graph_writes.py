"""Graph writes use actual native payloads and existing addressed leases."""

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.organize_graph import GraphProposal
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE


def address(key, kind=SurfaceKind.ISSUE_GRAPH):
    return WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=key))


def fixture():
    board = _Board()
    board.server.issues["child"] = FakeMcpIssue(id="child", parent_id=CLAIMED_ISSUE)
    board.server.issues["peer"] = FakeMcpIssue(id="peer", parent_id=CLAIMED_ISSUE)
    return board, board.tracker()


async def expected(tracker):
    return tuple(
        [
            graph_snapshot(await tracker.read_issue(issue_key=key))
            for key in (CLAIMED_ISSUE, "child", "peer")
        ]
    )


def changes(*rows):
    return GraphProposal.model_validate(
        {"kind": "graph", "issue_id": "child", "changes": rows}
    ).changes


@pytest.mark.parametrize(
    "relation,inverse", [("blockedBy", "blocks"), ("relatedTo", "relatedTo")]
)
async def test_actual_adapter_preserves_unrequested_edges_and_updates_inverse(
    relation, inverse
):
    board, tracker = fixture()
    board.server.issues["child"].relations = [("relatedTo", CLAIMED_ISSUE)]
    kind = "blocked_by" if relation == "blockedBy" else "related_to"
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="writer",
        surfaces=frozenset({address("child"), address("peer")}),
        lease_seconds=321.5,
    ):
        await tracker.update_issue_graph(
            issue_key="child",
            expected=await expected(tracker),
            changes=changes({"kind": kind, "add": ["peer"]}),
            holder="writer",
        )
        assert (relation, "peer") in board.server.issues["child"].relations
        assert (inverse, "child") in board.server.issues["peer"].relations
        assert ("relatedTo", CLAIMED_ISSUE) in board.server.issues["child"].relations
        await tracker.update_issue_graph(
            issue_key="child",
            expected=await expected(tracker),
            changes=changes({"kind": kind, "remove": ["peer"]}),
            holder="writer",
        )
    assert (relation, "peer") not in board.server.issues["child"].relations
    assert (inverse, "child") not in board.server.issues["peer"].relations
    writes = [args for name, args in board.calls if name == "save_issue"]
    assert writes == [
        {"id": "child", relation: ["peer"]},
        {"id": "child", "remove" + relation[0].upper() + relation[1:]: ["peer"]},
    ]


@pytest.mark.parametrize(
    "cause", ["missing_peer_grant", "stale_peer", "parent_cycle", "dependency_cycle"]
)
async def test_graph_refuses_before_save(cause):
    board, tracker = fixture()
    proposed = changes({"kind": "blocked_by", "add": ["peer"]})
    snapshot = await expected(tracker)
    held = {address("child"), address("peer")}
    if cause == "missing_peer_grant":
        held.remove(address("peer"))
    elif cause == "stale_peer":
        board.server.issues["peer"].description = "New source evidence"
    elif cause == "parent_cycle":
        proposed = changes({"kind": "parent", "parent_id": "child"})
    else:
        board.server.issues["peer"].relations = [("blockedBy", "child")]
        snapshot = await expected(tracker)
    async with RunSurfaceLease(
        tracker=tracker, job_id="writer", surfaces=frozenset(held), lease_seconds=321.5
    ):
        with pytest.raises((OrganizeWriteRefusalError, SurfaceLeaseError)):
            await tracker.update_issue_graph(
                issue_key="child", expected=snapshot, changes=proposed, holder="writer"
            )
    assert not [args for name, args in board.calls if name == "save_issue"]


async def test_split_creation_uses_native_initial_state_and_replay_never_overwrites():
    board, tracker = fixture()
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="writer",
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)}),
        lease_seconds=321.5,
    ):
        first = await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="stable/child",
            title="Actual split",
            body="Independent child specification",
            holder="writer",
            expected=await expected(tracker),
        )
        before = list(board.calls)
        repeated = await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="stable/child",
            title="Overwrite forbidden",
            body="Not the original",
            holder="writer",
            expected=await expected(tracker),
        )
        assert repeated == first
        assert not [
            args for name, args in board.calls[len(before) :] if name == "save_issue"
        ]
        assert first.parent_key == CLAIMED_ISSUE
        assert first.state_kind.value == "unstarted"
        assert not {"criterion", "decision"} & first.issue_labels
        assert await tracker.read_split_children(source_key=CLAIMED_ISSUE) == (first,)
    writes = [args for name, args in board.calls if name == "save_issue"]
    assert len(writes) == 1
    assert writes[0]["state"] == "fixture-team-Todo-id"
    assert writes[0]["parentId"] == CLAIMED_ISSUE
    assert "id" not in writes[0]
    assert "labels" not in writes[0]
    assert (
        await tracker.read_issue_identity(issue_key=first.issue_key)
    ).deliverable_key == "stable/child"


@pytest.mark.parametrize("kind", [SurfaceKind.ISSUE_GRAPH, SurfaceKind.ISSUE_SPLIT_SET])
@pytest.mark.parametrize(
    "container", [ScopeKind.PROJECT, ScopeKind.INITIATIVE, ScopeKind.MILESTONE]
)
def test_graph_surfaces_never_alias_container_or_parent_body(kind, container):
    with pytest.raises(ValueError):
        WritableSurface(kind=kind, ref=ScopeRef(kind=container, key=CLAIMED_ISSUE))
    assert address(CLAIMED_ISSUE, kind) != address(
        CLAIMED_ISSUE, SurfaceKind.ISSUE_DESCRIPTION
    )
    assert address(CLAIMED_ISSUE, SurfaceKind.ISSUE_GRAPH) != address(
        CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET
    )


@pytest.mark.parametrize(
    "change", ["parent", "dependency", "related", "priority", "body"]
)
async def test_split_source_drift_during_state_lookup_refuses_before_create(
    monkeypatch, change
):
    board, tracker = fixture()
    original = board.call_tool
    snapshot = await expected(tracker)

    async def call_tool(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "list_issue_statuses":
            source = board.server.issues[CLAIMED_ISSUE]
            if change == "parent":
                source.parent_id = "peer"
            elif change in {"dependency", "related"}:
                source.relations.append(
                    ("blockedBy" if change == "dependency" else "relatedTo", "peer")
                )
            elif change == "priority":
                source.priority_raw = 1
            else:
                source.description = "Unseen specification"
        return result

    monkeypatch.setattr(board, "call_tool", call_tool)
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="writer",
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)}),
        lease_seconds=321.5,
    ):
        with pytest.raises(OrganizeWriteRefusalError, match="native graph changed"):
            await tracker.create_split_if_absent(
                source_key=CLAIMED_ISSUE,
                deliverable_key="stable",
                title="Child",
                body="Authored body",
                holder="writer",
                expected=snapshot,
            )
    assert not [args for name, args in board.calls if name == "save_issue"]


@pytest.mark.parametrize("missing", [None, "", " \n "])
def test_initial_state_requires_actual_nonblank_native_identity(missing):
    from pydantic import ValidationError

    from kodezart.adapters.linear_mcp_types import LINEAR_WORKFLOW_STATES

    row = {"name": "Todo", "type": "unstarted"}
    if missing is not None:
        row["id"] = missing
    with pytest.raises(ValidationError):
        LINEAR_WORKFLOW_STATES.validate_python([row])


@pytest.mark.parametrize(
    "extra",
    [
        {"id": "existing"},
        {"id": None},
        {"issueId": "existing"},
        {"patch": []},
        {"project": None},
        {"unknownLocator": "existing"},
    ],
)
def test_split_creation_shape_never_bypasses_existing_issue_transition_guard(extra):
    from kodezart.adapters.linear_mcp_tracker import refuse_combined_issue_write
    from kodezart.core.errors import TrackerProtocolError

    payload = {
        "title": "Child",
        "description": "Body",
        "team": "native-team",
        "parentId": "source",
        "state": "native-unstarted",
    }
    refuse_combined_issue_write(payload)
    refuse_combined_issue_write({**payload, "project": "native-project"})
    with pytest.raises(TrackerProtocolError):
        refuse_combined_issue_write({**payload, **extra})


async def test_project_milestones_expose_complete_native_identities_and_descriptions():
    from tests.tracker.conftest import linear_over_fake_mcp
    from tests.tracker.test_scope_reads import MILESTONE, PROJECT, ScopeMcpServer

    server = ScopeMcpServer()
    tracker = linear_over_fake_mcp(server)
    milestones = await tracker.project_milestones(project_key=PROJECT.key)
    assert {item.ref.key for item in milestones} == {
        row["id"] for row in server.milestones[PROJECT.key]
    }
    assert all(item.parent == PROJECT and item.url is None for item in milestones)
    assert (
        next(item for item in milestones if item.ref == MILESTONE).description
        == f"Complete description of {MILESTONE.key}"
    )


@pytest.mark.parametrize("fault", ["duplicate", "wrong_detail", "changed_membership"])
async def test_project_milestone_incomplete_identity_evidence_refuses(
    monkeypatch, fault
):
    from kodezart.domain.errors import ScopeReadError
    from tests.tracker.conftest import linear_over_fake_mcp
    from tests.tracker.test_scope_reads import PROJECT, ScopeMcpServer

    server = ScopeMcpServer()
    original = server.call_tool
    calls = 0

    async def call_tool(*, name, arguments):
        nonlocal calls
        result = await original(name=name, arguments=arguments)
        if name == "list_milestones":
            calls += 1
            if fault == "duplicate":
                result = {
                    **result,
                    "milestones": [*result["milestones"], result["milestones"][0]],
                }
            if fault == "changed_membership" and calls == 2:
                result = {**result, "milestones": []}
        if name == "get_milestone" and fault == "wrong_detail":
            result = {**result, "id": "another-native-id"}
        return result

    monkeypatch.setattr(server, "call_tool", call_tool)
    with pytest.raises(ScopeReadError):
        await linear_over_fake_mcp(server).project_milestones(project_key=PROJECT.key)


async def test_parent_change_and_clear_use_exact_native_keys():
    board, tracker = fixture()
    surfaces = frozenset(address(key) for key in (CLAIMED_ISSUE, "child", "peer"))
    async with RunSurfaceLease(
        tracker=tracker, job_id="writer", surfaces=surfaces, lease_seconds=321.5
    ):
        await tracker.update_issue_graph(
            issue_key="child",
            expected=await expected(tracker),
            changes=changes({"kind": "parent", "parent_id": "peer"}),
            holder="writer",
        )
        assert board.server.issues["child"].parent_id == "peer"
        await tracker.update_issue_graph(
            issue_key="child",
            expected=await expected(tracker),
            changes=changes({"kind": "parent", "parent_id": None}),
            holder="writer",
        )
    assert board.server.issues["child"].parent_id is None
    assert [args for name, args in board.calls if name == "save_issue"] == [
        {"id": "child", "parentId": "peer"},
        {"id": "child", "parentId": None},
    ]


@pytest.mark.parametrize("milestone", ["new-native-milestone", "milestone-two", None])
async def test_milestone_assignment_requires_actual_current_project(
    monkeypatch, milestone
):
    from tests.tracker.conftest import linear_over_fake_mcp
    from tests.tracker.test_scope_reads import PROJECT, ROOT, ScopeMcpServer, _milestone

    server = ScopeMcpServer()
    from tests.tracker.conftest import FIXTURE_NOW

    server._comment_clock = lambda: FIXTURE_NOW
    server.issues["FOREIGN-ROOT"] = FakeMcpIssue(id="FOREIGN-ROOT")
    server.milestones[PROJECT.key].append(_milestone("new-native-milestone"))
    original = server._tool_save_issue
    writes = []

    def save(arguments):
        writes.append(dict(arguments))
        result = original(arguments)
        if "milestone" in arguments:
            server.issues[arguments["id"]].milestone_key = arguments["milestone"]
        return result

    monkeypatch.setattr(server, "_tool_save_issue", save)
    tracker = linear_over_fake_mcp(server)
    snapshot = tuple(
        [
            graph_snapshot(await tracker.read_issue(issue_key=key))
            for key in server.issues
        ]
    )
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="writer",
        surfaces=frozenset({address(ROOT.key)}),
        lease_seconds=321.5,
    ):
        proposal = changes({"kind": "milestone", "milestone_id": milestone})
        if milestone == "new-native-milestone":
            actual = await tracker.update_issue_graph(
                issue_key=ROOT.key, expected=snapshot, changes=proposal, holder="writer"
            )
            assert actual.milestone_key == milestone
            assert writes == [{"id": ROOT.key, "milestone": milestone}]
        else:
            with pytest.raises(OrganizeWriteRefusalError):
                await tracker.update_issue_graph(
                    issue_key=ROOT.key,
                    expected=snapshot,
                    changes=proposal,
                    holder="writer",
                )
            assert writes == []


@pytest.mark.parametrize("fault", ["duplicate", "wrong_parent", "criterion"])
async def test_existing_split_identity_damage_never_creates_or_overwrites(fault):
    board, tracker = fixture()
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="writer",
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)}),
        lease_seconds=321.5,
    ):
        first = await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="stable",
            title="Child",
            body="Original specification",
            holder="writer",
            expected=await expected(tracker),
        )
        native = board.server.issues[first.issue_key]
        if fault == "duplicate":
            board.server.issues["duplicate"] = FakeMcpIssue(
                id="duplicate", parent_id=CLAIMED_ISSUE, description=native.description
            )
        elif fault == "wrong_parent":
            native.parent_id = "peer"
        else:
            native.labels = ["acceptance-condition"]
        before = list(board.calls)
        from kodezart.domain.errors import DuplicateIssueIdentityError

        with pytest.raises((OrganizeWriteRefusalError, DuplicateIssueIdentityError)):
            await tracker.create_split_if_absent(
                source_key=CLAIMED_ISSUE,
                deliverable_key="stable",
                title="Overwrite",
                body="Wrong",
                holder="writer",
                expected=await expected(tracker),
            )
        assert not [
            args for name, args in board.calls[len(before) :] if name == "save_issue"
        ]
