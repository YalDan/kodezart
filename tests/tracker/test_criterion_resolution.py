"""Native key resolution retains current source and refuses ambiguity at the port."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import CriterionReadError, CriterionResolutionError
from kodezart.services.criterion_sources import resolve_criterion
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker import test_criterion_reader as fixtures
from tests.tracker.conftest import STATE_TYPES
from tests.tracker.test_criterion_reader_boundary import ChildPagesServer
from tests.tracker.test_linear_mcp_tracker import tracker_over

base_server = fixtures.server
PARENT = fixtures.PARENT
FIRST = fixtures.FIRST
SECOND = fixtures.SECOND


@pytest.fixture
def server(base_server):
    for state, kind in STATE_TYPES.items():
        key = f"configured-state/{state}"
        base_server.issues[key] = FakeMcpIssue(id=key, status=state, status_type=kind)
    return base_server


async def test_resolution_preserves_own_key_full_source_and_every_state(
    tracker, tracker_writes
):
    for state in (
        "Backlog",
        "Todo",
        "In Progress",
        "In Review",
        "Done",
        "Canceled",
        "Duplicate",
    ):
        await tracker.restore_workflow_state(issue_key=FIRST, state_name=state)
        before = tracker_writes()
        resolved = await resolve_criterion(
            tracker=tracker, issue_key=PARENT, criterion_key=FIRST
        )
        assert resolved == await tracker.read_issue(issue_key=FIRST)
        assert resolved.state_name == state and resolved.body.endswith(
            fixtures.EVIDENCE
        )
        assert tracker_writes() == before


async def test_identical_text_and_parent_prose_cannot_redirect_the_native_key(
    tracker, tracker_writes
):
    first = await tracker.read_issue(issue_key=FIRST)
    await tracker.update_issue(issue_key=SECOND, body=first.body)
    await tracker.update_issue(issue_key=PARENT, body=f"- [x] {SECOND}: {first.body}")
    before = tracker_writes()
    for key in (FIRST, SECOND):
        resolved = await resolve_criterion(
            tracker=tracker, issue_key=PARENT, criterion_key=key
        )
        assert resolved.issue_key == key and resolved.body == first.body
    assert tracker_writes() == before


@pytest.mark.parametrize(
    "key", ["absent/二", "ordinary/1", "grandchild/1", "other-parent/1"]
)
async def test_no_current_direct_criterion_refuses_with_both_identities_and_no_write(
    tracker, tracker_writes, key
):
    before = tracker_writes()
    with pytest.raises(CriterionResolutionError, match="0 current") as raised:
        await resolve_criterion(tracker=tracker, issue_key=PARENT, criterion_key=key)
    assert raised.value.issue_key == PARENT and raised.value.criterion_key == key
    assert PARENT in str(raised.value) and key in str(raised.value)
    assert tracker_writes() == before


async def test_successful_empty_family_is_not_an_unreadable_family(
    tracker, tracker_writes
):
    before = tracker_writes()
    with pytest.raises(CriterionResolutionError, match="0 current") as empty:
        await resolve_criterion(tracker=tracker, issue_key=SECOND, criterion_key=FIRST)
    assert empty.value.__cause__ is None
    with pytest.raises(CriterionResolutionError, match="unreadable") as unreadable:
        await resolve_criterion(
            tracker=tracker, issue_key="missing/parent", criterion_key=FIRST
        )
    assert unreadable.value.issue_key == "missing/parent"
    assert unreadable.value.criterion_key == FIRST
    assert isinstance(unreadable.value.__cause__, CriterionReadError)
    assert tracker_writes() == before


@pytest.mark.parametrize("change", ["label", "parent"])
async def test_each_resolution_reads_current_native_membership(
    tracker, server, tracker_writes, change
):
    assert (
        await resolve_criterion(tracker=tracker, issue_key=PARENT, criterion_key=FIRST)
    ).issue_key == FIRST
    if isinstance(tracker, FakeTrackerPort):
        update = (
            {"issue_labels": frozenset()}
            if change == "label"
            else {"parent_key": SECOND}
        )
        tracker.issues[FIRST] = tracker.issues[FIRST].model_copy(update=update)
    elif change == "label":
        server.issues[FIRST].labels = []
    else:
        server.issues[FIRST].parent_id = SECOND
    before = tracker_writes()
    with pytest.raises(CriterionResolutionError, match="0 current"):
        await resolve_criterion(tracker=tracker, issue_key=PARENT, criterion_key=FIRST)
    assert tracker_writes() == before


@pytest.mark.parametrize(
    "damage", ["multiple", "other-duplicate", "wrong-parent", "wrong-label"]
)
async def test_nonconforming_family_never_selects_a_first_or_neighbor(
    tracker, tracker_writes, monkeypatch, damage
):
    rows = list(await tracker.read_criteria(issue_key=PARENT))
    assert [row.issue_key for row in rows] == [FIRST, SECOND]
    if damage == "multiple":
        rows.append(rows[0])
    elif damage == "other-duplicate":
        rows.append(rows[1])
    else:
        update = (
            {"parent_key": "foreign"}
            if damage == "wrong-parent"
            else {"issue_labels": frozenset()}
        )
        rows[1] = rows[1].model_copy(update=update)
    monkeypatch.setattr(tracker, "read_criteria", AsyncMock(return_value=rows))
    before = tracker_writes()
    with pytest.raises(CriterionResolutionError) as raised:
        await resolve_criterion(tracker=tracker, issue_key=PARENT, criterion_key=FIRST)
    assert raised.value.issue_key == PARENT and raised.value.criterion_key == FIRST
    assert (
        "2 current" if damage == "multiple" else "ambiguous membership"
    ) in raised.value.reason
    assert tracker_writes() == before


async def test_cancellation_is_not_reclassified_as_resolution_failure(
    tracker, monkeypatch, tracker_writes
):
    monkeypatch.setattr(
        tracker, "read_criteria", AsyncMock(side_effect=asyncio.CancelledError)
    )
    before = tracker_writes()
    with pytest.raises(asyncio.CancelledError):
        await resolve_criterion(tracker=tracker, issue_key=PARENT, criterion_key=FIRST)
    assert tracker_writes() == before


async def test_native_last_page_and_duplicate_page_overlap_preserve_one_full_object():
    key = "condition/二"
    parent = "PARENT/1"
    body = "**Check:** " + "complete native body " * 500 + "\n**Evidence:** tail"
    child = FakeMcpIssue(
        id=key, parent_id=parent, labels=[fixtures.LABEL], description=body
    )
    native = ChildPagesServer(
        children=[child],
        pages={
            None: {"issues": [], "hasNextPage": True, "cursor": "next"},
            "next": {
                "issues": [{"id": key, "description": "truncated"}],
                "hasNextPage": True,
                "cursor": "last",
            },
            "last": {"issues": [{"id": key}], "hasNextPage": False},
        },
    )
    resolved = await resolve_criterion(
        tracker=tracker_over(native), issue_key=parent, criterion_key=key
    )
    assert resolved.issue_key == key and resolved.body == body
    assert [call.get("cursor") for call in native.tool_calls("list_issues")] == [
        None,
        "next",
        "last",
    ]
    assert (
        native.tool_calls("save_issue") == []
        and native.tool_calls("save_comment") == []
    )


async def test_native_incomplete_family_cannot_become_a_missing_key():
    native = ChildPagesServer(pages={None: {"issues": [], "hasNextPage": True}})
    with pytest.raises(CriterionResolutionError, match="unreadable") as raised:
        await resolve_criterion(
            tracker=tracker_over(native),
            issue_key="PARENT/1",
            criterion_key="condition/二",
        )
    assert (
        raised.value.issue_key == "PARENT/1"
        and raised.value.criterion_key == "condition/二"
    )
    assert isinstance(raised.value.__cause__, CriterionReadError)
    assert (
        native.tool_calls("save_issue") == []
        and native.tool_calls("save_comment") == []
    )
