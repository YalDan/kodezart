"""Complete configured membership is independent of the queue scan."""

import asyncio

import pytest

from kodezart.core.errors import McpTransportError, TrackerUnavailableError
from kodezart.domain.errors import IssueLabelReadError
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import FakeMcpIssue
from tests.model_members import CLASSIFICATION, NATIVE_MARKER, model_workspace


@pytest.fixture(params=["native", "fake"])
async def workspace(request):
    return await model_workspace(request.param)


async def test_complete_query_keeps_every_state_parent_and_foreign_team(workspace):
    for index, state in enumerate(
        (
            "triage",
            "backlog",
            "unstarted",
            "started",
            "completed",
            "canceled",
            "duplicate",
        )
    ):
        await workspace.put(
            FakeMcpIssue(
                id=f"member/{index}",
                labels=[NATIVE_MARKER],
                description=f"Body {index}: unchanged bytes\r\n💡",
                status=f"native-{state}",
                status_type=state,
                team=f"unconfigured-team-{index}",
                parent_id=f"independent-parent/{index}",
            )
        )
    await workspace.put(FakeMcpIssue(id="unmarked", labels=["criterion_lifecycle"]))
    members = await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION)
    assert [item.issue_key for item in members] == [f"member/{i}" for i in range(7)]
    assert [item.body for item in members] == [
        f"Body {i}: unchanged bytes\r\n💡" for i in range(7)
    ]
    queries = [args for name, args in workspace.server.calls if name == "list_issues"]
    if workspace.tracker is workspace.native:
        assert len(queries) == 4
        assert all(args["includeArchived"] is True for args in queries)
        assert all(args["label"] == NATIVE_MARKER for args in queries)
        assert all(args["fields"] == ["id"] for args in queries)
        assert all("team" not in args and "state" not in args for args in queries)
    workspace.read_only()


async def test_empty_add_remove_and_cold_reads_follow_the_label(workspace):
    assert (
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION) == ()
    )
    item = FakeMcpIssue(id="added", labels=[NATIVE_MARKER])
    await workspace.put(item)
    assert [
        x.issue_key
        for x in await workspace.tracker.read_labeled_issues(
            classification=CLASSIFICATION
        )
    ] == ["added"]
    item.labels.clear()
    await workspace.put(item)
    assert (
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION) == ()
    )
    workspace.read_only()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_cursor",
        "repeated_cursor",
        "duplicate",
        "missing_labels",
        "missing_relations",
        "wrong_identity",
        "lost_label",
        "unknown_state",
        "transport",
    ],
)
async def test_native_unreadable_or_contradictory_membership_refuses(
    failure, monkeypatch
):
    workspace = await model_workspace("native")
    for index in range(4):
        await workspace.put(FakeMcpIssue(id=f"member/{index}", labels=[NATIVE_MARKER]))
    original = workspace.server.call_tool

    async def corrupted(*, name, arguments):
        if failure == "repeated_cursor" and name == "list_issues":
            return {"issues": [], "hasNextPage": True, "cursor": "same"}
        if failure == "transport" and name == "list_issues":
            raise McpTransportError(
                "membership transport unavailable",
                server_name="fixture",
                tool_name=name,
            )
        response = await original(name=name, arguments=arguments)
        if name == "list_issues":
            if failure == "missing_cursor":
                response.pop("cursor", None)
            elif failure == "repeated_cursor":
                response.update(issues=[], hasNextPage=True, cursor="same")
            elif failure == "duplicate":
                response["issues"].append(response["issues"][0])
        if name == "get_issue":
            if failure == "missing_labels":
                response.pop("labels", None)
            elif failure == "missing_relations":
                response.pop("relations", None)
            elif failure == "wrong_identity":
                response["id"] = "another"
            elif failure == "lost_label":
                response["labels"] = []
            elif failure == "unknown_state":
                response["statusType"] = "future-state"
        return response

    monkeypatch.setattr(workspace.server, "call_tool", corrupted)
    with pytest.raises(IssueLabelReadError) as raised:
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION)
    assert raised.value.classification == CLASSIFICATION
    workspace.read_only()


async def test_missing_config_refuses_before_native_io():
    workspace = await model_workspace("native")
    with pytest.raises(OperationMemberAbsentError, match="issue_labels"):
        await workspace.tracker.read_labeled_issues(classification="absent")
    assert workspace.server.calls == []


async def test_changed_member_during_full_read_refuses_in_both_adapters(
    workspace, monkeypatch
):
    await workspace.put(FakeMcpIssue(id="member", labels=[NATIVE_MARKER]))
    original = workspace.tracker.read_planning_issue

    async def changed(*, issue_key):
        issue = await original(issue_key=issue_key)
        return issue.model_copy(update={"issue_labels": frozenset()})

    monkeypatch.setattr(workspace.tracker, "read_planning_issue", changed)
    with pytest.raises(IssueLabelReadError, match="label changed"):
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION)


async def test_failed_later_page_does_not_return_the_first_page(monkeypatch):
    workspace = await model_workspace("native")
    for index in range(3):
        await workspace.put(FakeMcpIssue(id=f"member/{index}", labels=[NATIVE_MARKER]))
    original = workspace.server.call_tool

    async def unavailable(*, name, arguments):
        if name == "list_issues" and arguments.get("cursor"):
            raise McpTransportError("later page unavailable", server_name="fixture")
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(workspace.server, "call_tool", unavailable)
    with pytest.raises(IssueLabelReadError) as raised:
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION)
    assert isinstance(raised.value.__cause__, TrackerUnavailableError)
    assert isinstance(raised.value.__cause__.__cause__, McpTransportError)


async def test_native_cancellation_is_not_an_empty_set(monkeypatch):
    workspace = await model_workspace("native")

    async def canceled(*, name, arguments):
        raise asyncio.CancelledError

    monkeypatch.setattr(workspace.server, "call_tool", canceled)
    with pytest.raises(asyncio.CancelledError):
        await workspace.tracker.read_labeled_issues(classification=CLASSIFICATION)
