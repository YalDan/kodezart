"""Empty membership is one reading; an empty fire cannot start the real loop."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from kodezart.domain.errors import CriterionReadError, EmptyFireCriteriaError
from tests.chains.test_ralph_loop import _make_loop, _run_kwargs
from tests.fakes import (
    FakeAgentExecutor,
    FakeGitService,
    FakeMcpIssue,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
)
from tests.tracker.conftest import fixture_server
from tests.tracker.test_criterion_reader_boundary import ChildPagesServer
from tests.tracker.test_linear_mcp_tracker import tracker_over

PARENT = "empty-subject/1"
CHILD = "criterion/one"
LABEL = "acceptance-condition"


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[PARENT] = FakeMcpIssue(id=PARENT)
    return server


@pytest.mark.parametrize(
    "body",
    [
        "No checklist heading was authored.",
        "## Acceptance criteria\n\nNo lines.",
        "## Acceptance criteria\n\n- [x] A parent checkbox cannot mint a child.",
        "",
    ],
)
async def test_parent_heading_shapes_all_read_empty_and_refuse_fire(
    tracker, tracker_writes, body
):
    await tracker.update_issue(issue_key=PARENT, body=body)
    writes = tracker_writes()
    assert tuple(await tracker.read_criteria(issue_key=PARENT)) == ()
    with pytest.raises(EmptyFireCriteriaError) as caught:
        await tracker.read_fire_spec(issue_key=PARENT)
    assert caught.value.issue_key == PARENT
    assert tracker_writes() == writes


async def test_once_present_then_absent_criteria_have_the_same_empty_reading(
    tracker, server, tracker_writes
):
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[CHILD] = tracker.issues[PARENT].model_copy(
            update={
                "issue_key": CHILD,
                "parent_key": PARENT,
                "issue_labels": frozenset({"criterion"}),
                "body": "**Check:** Observable behavior.",
            }
        )
    else:
        server.issues[CHILD] = FakeMcpIssue(
            id=CHILD,
            parent_id=PARENT,
            labels=[LABEL],
            description="**Check:** Observable behavior.",
        )
    assert (await tracker.read_fire_spec(issue_key=PARENT)).criteria == (CHILD,)
    if isinstance(tracker, FakeTrackerPort):
        del tracker.issues[CHILD]
    else:
        del server.issues[CHILD]
    writes = tracker_writes()
    assert tuple(await tracker.read_criteria(issue_key=PARENT)) == ()
    with pytest.raises(EmptyFireCriteriaError):
        await tracker.read_fire_spec(issue_key=PARENT)
    assert tracker_writes() == writes


@pytest.mark.parametrize("reader", ["read_criteria", "read_fire_spec"])
async def test_unreadable_subject_cannot_become_a_successful_empty_set(tracker, reader):
    with pytest.raises(CriterionReadError) as caught:
        await getattr(tracker, reader)(issue_key="missing-subject")
    assert caught.value.issue_key == "missing-subject"


@pytest.mark.parametrize("reader", ["read_criteria", "read_fire_spec"])
async def test_incomplete_successful_first_page_never_becomes_empty(reader):
    server = ChildPagesServer(
        pages={
            None: {"issues": [], "hasNextPage": True, "cursor": "last"},
            "last": {"issues": [], "hasNextPage": True},
        }
    )
    with pytest.raises(CriterionReadError, match="pagination"):
        await getattr(tracker_over(server), reader)(issue_key="PARENT/1")
    assert server.tool_calls("save_issue") == []


async def test_successful_empty_membership_cannot_dispatch_the_actual_loop(
    tracker, monkeypatch
):
    """The public entry raises before graph dispatch, sessions or Git activity."""
    empty = list(await tracker.read_criteria(issue_key=PARENT))
    assert empty == []
    executor = FakeAgentExecutor([])
    workspace = FakeWorkspaceProvider()
    git = FakeGitService()
    cache = FakeRepoCache()
    loop = _make_loop(executor=executor, workspace=workspace, git=git, cache=cache)
    dispatch = Mock(side_effect=AssertionError("the graph must not start"))
    monkeypatch.setattr(loop._compiled, "astream", dispatch)
    arguments = _run_kwargs()
    arguments["acceptance_criteria"] = empty
    events = []
    with pytest.raises(ValidationError) as caught:
        async for event in loop.run(**arguments):
            events.append(event)
    assert any(
        error["loc"] == ("acceptance_criteria",) and error["type"] == "too_short"
        for error in caught.value.errors()
    )
    assert events == []
    dispatch.assert_not_called()
    assert executor.calls == []
    assert workspace.calls == []
    assert git.calls == []
    assert cache.calls == []
