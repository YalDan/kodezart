"""Native creation capability: real adapter and lease, only MCP is doubled."""

import pytest

from kodezart.adapters.linear_mcp_tracker import refuse_combined_issue_write
from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import CriterionReadError, SurfaceLeaseError
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeMcpIssue
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE

JOB = "criterion-preparation-job"
CHECK = "The prepared artifact is byte-identical to the declared input."
DO = "Compare the committed bytes to the declared input."


def surface(kind=SurfaceKind.CRITERION_CHILD_SET):
    return WritableSurface(
        kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=CLAIMED_ISSUE)
    )


async def create(tracker, **changes):
    fields = {
        "parent_key": CLAIMED_ISSUE,
        "title": "Preserve input bytes",
        "check": CHECK,
        "do": DO,
        "holder": JOB,
    }
    fields.update(changes)
    return await tracker.create_criterion_if_absent(**fields)


def saves(board):
    return [args for name, args in board.calls if name == "save_issue"]


async def test_native_create_initializes_a_new_todo_criterion_then_replays_no_write():
    board = _Board()
    tracker = board.tracker()
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    ):
        created = await create(tracker)
    assert created.parent_key == CLAIMED_ISSUE
    assert created.state_kind is WorkflowStateKind.UNSTARTED
    assert created.issue_labels == frozenset({"criterion"})
    assert created.body == f"**Check:** {CHECK}\n\n**Do:** {DO}\n\n**Evidence:**\n"
    assert len(saves(board)) == 1
    assert saves(board)[0] == {
        "title": "Preserve input bytes",
        "description": created.body,
        "team": "fixture-team",
        "parentId": CLAIMED_ISSUE,
        "labels": ["acceptance-condition"],
        "state": "fixture-team-Todo-id",
    }
    board.server.issues[
        created.issue_key
    ].description += "Existing evidence must survive."
    board.server.issues[created.issue_key].status = "Done"
    board.server.issues[created.issue_key].status_type = "completed"
    board.calls.clear()
    replay = await create(
        tracker, title="Never overwrite this", do="Never overwrite this either"
    )
    assert replay.issue_key == created.issue_key
    assert replay.body.endswith("Existing evidence must survive.")
    assert replay.state_kind is WorkflowStateKind.COMPLETED
    assert saves(board) == []
    assert not any(name == "save_comment" for name, _ in board.calls)


@pytest.mark.parametrize("kind", [None, SurfaceKind.ISSUE_DESCRIPTION])
async def test_missing_or_other_surface_grant_cannot_authorize_creation(kind):
    board = _Board()
    tracker = board.tracker()
    if kind is None:
        with pytest.raises(SurfaceLeaseError):
            await create(tracker)
    else:
        async with RunSurfaceLease(
            tracker=tracker,
            job_id=JOB,
            surfaces=frozenset({surface(kind)}),
            lease_seconds=300,
        ):
            with pytest.raises(SurfaceLeaseError):
                await create(tracker)
    assert saves(board) == []


@pytest.mark.parametrize("states", [[], ["Todo", "Another Todo"]])
async def test_ambiguous_or_absent_native_initial_state_refuses_before_write(states):
    board = _Board()
    board.server.statuses["fixture-team"] = states
    board.server.state_types["Another Todo"] = "unstarted"
    with pytest.raises(CriterionReadError, match="exactly one unstarted"):
        await create(board.tracker())
    assert saves(board) == []


async def test_duplicate_check_identity_refuses_before_any_write():
    board = _Board()
    for key in ("criterion-a", "criterion-b"):
        board.server.issues[key] = FakeMcpIssue(
            id=key,
            parent_id=CLAIMED_ISSUE,
            labels=["acceptance-condition"],
            description=f"**Check:** {CHECK}\n\n**Do:** {DO}\n\n**Evidence:**\n",
        )
    with pytest.raises(CriterionReadError, match="duplicate current Check"):
        await create(board.tracker())
    assert saves(board) == []


@pytest.mark.parametrize("check", ["", "Check\n\n**Evidence:** fabricated"])
async def test_invalid_authored_fields_refuse_before_any_native_call(check):
    board = _Board()
    with pytest.raises(CriterionReadError):
        await create(board.tracker(), check=check)
    assert board.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"id": "existing"},
        {"issueId": "existing"},
        {"patch": []},
        {"parentId": ""},
        {"labels": "label"},
        {"title": None},
    ],
)
def test_create_initialization_exception_cannot_admit_an_update_or_malformed_shape(
    change,
):
    payload = {
        "title": "new",
        "description": "new body",
        "team": "team",
        "parentId": "parent",
        "labels": ["criterion"],
        "state": "Todo",
        **change,
    }
    with pytest.raises(TrackerProtocolError, match="separate writes"):
        refuse_combined_issue_write(payload)


async def test_backend_can_land_an_already_issued_create_after_expiry():
    """Characterize the known backend limit; this is not an atomic fence claim."""
    import asyncio

    board = _Board()
    tracker = board.tracker()
    board.pause = lambda name, args: name == "save_issue" and "id" not in args
    lease = RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    )
    await lease.__aenter__()
    task = asyncio.create_task(create(tracker))
    await asyncio.wait_for(board.reached.wait(), timeout=3)
    board.advance(301)
    async with RunSurfaceLease(
        tracker=tracker,
        job_id="successor",
        surfaces=frozenset({surface()}),
        lease_seconds=300,
    ):
        board.resume.set()
        created = await task
        assert created.parent_key == CLAIMED_ISSUE
        assert board.server.issues[created.issue_key].status_type == "unstarted"
    await lease.__aexit__(None, None, None)
    assert len(saves(board)) == 1


async def test_unknown_create_response_preserves_committed_child_without_resend(
    monkeypatch,
):
    from kodezart.core.errors import McpCallUnansweredError, TrackerUnavailableError

    board = _Board()
    tracker = board.tracker()
    original = board.call_tool
    sent = []

    async def unanswered(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "save_issue" and "id" not in arguments:
            sent.append(dict(arguments))
            raise McpCallUnansweredError(
                "created but response lost", server_name="fixture", tool_name=name
            )
        return result

    monkeypatch.setattr(board, "call_tool", unanswered)
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    ):
        with pytest.raises(TrackerUnavailableError) as caught:
            await create(tracker)
    assert isinstance(caught.value.__cause__, McpCallUnansweredError)
    assert len(sent) == 1
    children = [
        issue
        for issue in board.server.issues.values()
        if issue.parent_id == CLAIMED_ISSUE
    ]
    assert len(children) == 1
    replay = await create(tracker)
    assert replay.issue_key == children[0].id
    assert len(sent) == 1


@pytest.mark.parametrize("damage", ["missing", "empty", "blank"])
async def test_initial_state_requires_actual_nonblank_native_id(monkeypatch, damage):
    board = _Board()
    tracker = board.tracker()
    original = board.call_tool

    async def damaged(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "list_issue_statuses":
            result = [dict(row) for row in result]
            for row in result:
                if row["type"] == "unstarted":
                    if damage == "missing":
                        del row["id"]
                    else:
                        row["id"] = "" if damage == "empty" else " \n"
        return result

    monkeypatch.setattr(board, "call_tool", damaged)
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    ):
        with pytest.raises(TrackerProtocolError, match="invalid initial state"):
            await create(tracker)
    assert saves(board) == []


async def test_initial_state_uses_the_selected_team_id_not_shared_name(monkeypatch):
    board = _Board()
    tracker = board.tracker()
    original = board.call_tool
    selected = []

    async def observed(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "list_issue_statuses":
            selected.append(arguments["team"])
        return result

    monkeypatch.setattr(board, "call_tool", observed)
    async with RunSurfaceLease(
        tracker=tracker, job_id=JOB, surfaces=frozenset({surface()}), lease_seconds=300
    ):
        child = await create(tracker)
    assert selected == ["fixture-team"]
    assert saves(board)[0]["state"] == "fixture-team-Todo-id"
    assert child.state_kind is WorkflowStateKind.UNSTARTED
