"""Independent fresh-boundary controls over the production graph adapter."""

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError, SurfaceLeaseError
from kodezart.domain.organize_graph import graph_snapshot
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.surface import SurfaceKind
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import CLAIMED_ISSUE, FIXTURE_NOW, linear_over_fake_mcp
from tests.tracker.test_organize_graph_writes import address, changes, expected, fixture


def saves(board):
    return [args for name, args in board.calls if name == "save_issue"]


@pytest.mark.parametrize("expire", [False, True])
async def test_graph_lease_must_still_hold_after_final_graph_read(monkeypatch, expire):
    board, tracker = fixture()
    snapshot = await expected(tracker)
    original = board.call_tool
    reads = 0

    async def call_tool(*, name, arguments):
        nonlocal reads
        result = await original(name=name, arguments=arguments)
        if name == "get_issue":
            reads += 1
            if reads == 6 and expire:
                board.advance(400)
        return result

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address("child")}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        error = None
        try:
            await tracker.update_issue_graph(
                issue_key="child",
                expected=snapshot,
                changes=changes({"kind": "priority", "priority": "urgent"}),
                holder="actual-job",
            )
        except SurfaceLeaseError as exc:
            error = exc
        assert reads >= 6
        if expire:
            assert saves(board) == [], (
                "save was issued after the final awaited read expired ownership"
            )
            assert error is not None
        else:
            assert error is None
            assert saves(board) == [{"id": "child", "priority": 1}]
    assert board.grants() == []


@pytest.mark.parametrize("change", [None, "body", "priority", "parent", "expiry"])
async def test_split_final_observed_source_is_checked_before_creation(
    monkeypatch, change
):
    board, tracker = fixture()
    snapshot = await expected(tracker)
    original = board.call_tool
    after_state = False
    source_reads = 0
    injected = False

    async def call_tool(*, name, arguments):
        nonlocal after_state, source_reads, injected
        if name == "list_issue_statuses":
            after_state = True
        if after_state and name == "get_issue" and arguments["id"] == CLAIMED_ISSUE:
            source_reads += 1
            # The first is the source within _read_unchanged_graph; the second
            # is create_split_if_absent's separate last current-source read.
            if source_reads == 2:
                injected = True
                source = board.server.issues[CLAIMED_ISSUE]
                if change == "body":
                    source.description = (
                        "Current specification changed while awaiting the final read"
                    )
                elif change == "priority":
                    source.priority_raw = 1
                elif change == "parent":
                    source.parent_id = "peer"
                elif change == "expiry":
                    board.advance(400)
        return await original(name=name, arguments=arguments)

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        error = None
        try:
            await tracker.create_split_if_absent(
                source_key=CLAIMED_ISSUE,
                deliverable_key="new-deliverable",
                title="Child",
                body="Prepared from the original source",
                holder="actual-job",
                expected=snapshot,
            )
        except (OrganizeWriteRefusalError, SurfaceLeaseError) as exc:
            error = exc
        assert injected, "the actual separate final source read was not reached"
        if change is not None:
            assert saves(board) == [], (
                "creation used a final source observation that no longer matched"
            )
            assert error is not None
        else:
            assert error is None
            assert len(saves(board)) == 1
    assert board.grants() == []


async def test_split_identity_appearing_during_state_read_is_not_duplicated(
    monkeypatch,
):
    board, tracker = fixture()
    surface = address(CLAIMED_ISSUE, SurfaceKind.ISSUE_SPLIT_SET)
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({surface}),
        lease_seconds=321.5,
    ):
        child = await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="stable",
            title="Existing child",
            body="Existing source-grounded specification",
            holder="actual-job",
            expected=await expected(tracker),
        )
    native = board.server.issues.pop(child.issue_key)
    board.calls.clear()
    snapshot = await expected(tracker)
    original = board.call_tool
    injected = False

    async def call_tool(*, name, arguments):
        nonlocal injected
        result = await original(name=name, arguments=arguments)
        if name == "list_issue_statuses":
            board.server.issues[child.issue_key] = native
            injected = True
        return result

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({surface}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(board, "call_tool", call_tool)
        returned = await tracker.create_split_if_absent(
            source_key=CLAIMED_ISSUE,
            deliverable_key="stable",
            title="Must not overwrite",
            body="Must not overwrite",
            holder="actual-job",
            expected=snapshot,
        )
        assert injected
        assert saves(board) == [], (
            "a stable split identity that appeared during awaited preparation "
            "was duplicated"
        )
        assert returned.issue_key == child.issue_key
    assert board.grants() == []


@pytest.mark.parametrize("move", [False, True])
async def test_milestone_parent_is_current_after_holder_wait(monkeypatch, move):
    from tests.tracker.test_scope_reads import (
        OTHER_PROJECT,
        PROJECT,
        ROOT,
        ScopeMcpServer,
        _milestone,
    )

    server = ScopeMcpServer()
    server._comment_clock = lambda: FIXTURE_NOW
    server.issues["FOREIGN-ROOT"] = FakeMcpIssue(id="FOREIGN-ROOT")
    milestone = _milestone("new-native-milestone")
    server.milestones[PROJECT.key].append(milestone)
    original_save = server._tool_save_issue
    writes = []

    def save(arguments):
        writes.append(dict(arguments))
        result = original_save(arguments)
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
    original = server.call_tool
    read_milestone = False
    injected = False

    async def call_tool(*, name, arguments):
        nonlocal read_milestone, injected
        result = await original(name=name, arguments=arguments)
        if name == "get_milestone" and arguments.get("query") == milestone["id"]:
            read_milestone = True
        if name == "list_comments" and read_milestone and move and not injected:
            server.milestones[PROJECT.key].remove(milestone)
            server.milestones[OTHER_PROJECT].append(milestone)
            injected = True
        return result

    async with RunSurfaceLease(
        tracker=tracker,
        job_id="actual-job",
        surfaces=frozenset({address(ROOT.key)}),
        lease_seconds=321.5,
    ):
        monkeypatch.setattr(server, "call_tool", call_tool)
        error = None
        try:
            await tracker.update_issue_graph(
                issue_key=ROOT.key,
                expected=snapshot,
                changes=changes({"kind": "milestone", "milestone_id": milestone["id"]}),
                holder="actual-job",
            )
        except OrganizeWriteRefusalError as exc:
            error = exc
        if move:
            assert injected
            assert writes == [], (
                "a milestone moved to another project while awaiting holder proof "
                "was assigned"
            )
            assert error is not None
        else:
            assert error is None
            assert writes == [{"id": ROOT.key, "milestone": milestone["id"]}]
