"""Native state entry timestamps survive general issue edits without restamping."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.errors import ScopeReadError
from kodezart.services.audit_collection import collect_audit_candidates
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import fixture_server, linear_over_fake_mcp

ROOT = "audit/root"
CHILD = "audit/criterion"
CREATED = datetime(2026, 1, 1, tzinfo=UTC)
CHANGED = CREATED + timedelta(seconds=20)
UPDATED = CREATED + timedelta(seconds=100)
SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key=ROOT)


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[ROOT] = FakeMcpIssue(
        id=ROOT,
        created_at=CREATED,
        state_changed_at=CHANGED,
        updated_at=UPDATED,
        status="Todo",
        status_type="unstarted",
    )
    server.issues[CHILD] = FakeMcpIssue(
        id=CHILD,
        parent_id=ROOT,
        labels=["acceptance-condition"],
        created_at=CREATED,
        state_changed_at=CREATED,
        updated_at=UPDATED,
        status="Done",
        status_type="completed",
    )
    return server


async def test_state_stamp_is_native_and_read_only(tracker, tracker_writes):
    writes = tracker_writes()
    first = await tracker.read_issue_state_change(issue_key=ROOT)
    assert first.issue.issue_key == ROOT
    assert first.state_changed_at == CHANGED
    assert first.state_changed_at != first.issue.created_at
    assert first.state_changed_at != first.issue.updated_at
    assert await tracker.read_issue_state_change(issue_key=ROOT) == first
    assert tracker_writes() == writes


async def test_created_issue_has_initial_state_history(tracker):
    issue = await tracker.create_issue(
        title="new audit subject",
        body="body",
        team_key="engineering",
        priority=IssuePriority.NONE,
    )
    revision = await tracker.read_issue_state_change(issue_key=issue.issue_key)
    assert revision.state_changed_at == issue.created_at
    await tracker.update_issue(issue_key=issue.issue_key, body="changed body")
    assert (
        await tracker.read_issue_state_change(issue_key=issue.issue_key)
    ).state_changed_at == revision.state_changed_at


@pytest.mark.parametrize("edit", ["body", "comment", "label"])
async def test_non_state_changes_do_not_move_state_entry_time(tracker, edit):
    before = await tracker.read_issue_state_change(issue_key=ROOT)
    if edit == "body":
        await tracker.update_issue(issue_key=ROOT, body="amended body")
    elif edit == "comment":
        await tracker.post_comment(issue_key=ROOT, body="ordinary comment")
    else:
        await tracker.set_queue_state(issue_key=ROOT, state=QueueState.PROPOSED)
    after = await tracker.read_issue_state_change(issue_key=ROOT)
    assert after.state_changed_at == before.state_changed_at
    assert after.issue.updated_at > before.issue.updated_at


async def test_state_transition_moves_stamp_and_unchanged_replay_does_not(
    tracker, monkeypatch
):
    before = await tracker.read_issue_state_change(issue_key=ROOT)
    await tracker.set_workflow_state(issue_key=ROOT, stage=LifecycleStage.IN_PROGRESS)
    after = await tracker.read_issue_state_change(issue_key=ROOT)
    assert after.state_changed_at > before.state_changed_at
    assert after.state_changed_at == after.issue.updated_at
    await tracker.set_workflow_state(issue_key=ROOT, stage=LifecycleStage.IN_PROGRESS)
    assert await tracker.read_issue_state_change(issue_key=ROOT) == after
    if isinstance(tracker, FakeTrackerPort):
        monkeypatch.setattr(
            tracker, "_clock", lambda: after.state_changed_at + timedelta(seconds=1)
        )
    await tracker.restore_workflow_state(issue_key=ROOT, state_name="Todo")
    restored = await tracker.read_issue_state_change(issue_key=ROOT)
    assert restored.state_changed_at > after.state_changed_at


async def test_collector_reads_every_issue_and_criterion_without_writes(
    tracker, tracker_writes
):
    writes = tracker_writes()
    candidates = await collect_audit_candidates(tracker=tracker, scope=SCOPE)
    assert [(row.issue_key, row.state_changed_at) for row in candidates] == [
        (CHILD, CREATED),
        (ROOT, CHANGED),
    ]
    assert tracker_writes() == writes


@pytest.mark.parametrize(
    "damage",
    [
        "omitted",
        "empty",
        "two_open",
        "none_open",
        "missing_end",
        "wrong_name",
        "wrong_kind",
        "wrong_issue",
        "naive_start",
        "before_creation",
        "after_update",
        "closed_after_current",
        "reversed_closed",
    ],
)
async def test_damaged_native_history_is_typed_refusal(server, monkeypatch, damage):
    payload = deepcopy(server.issues[ROOT].wire())
    history = payload["stateHistory"]
    entry = history[0]
    match damage:
        case "omitted":
            del payload["stateHistory"]
        case "empty":
            payload["stateHistory"] = []
        case "two_open":
            history.append(deepcopy(entry))
        case "none_open":
            entry["endedAt"] = UPDATED.isoformat()
        case "missing_end":
            del entry["endedAt"]
        case "wrong_name":
            entry["state"]["name"] = "Done"
        case "wrong_kind":
            entry["state"]["type"] = "completed"
        case "wrong_issue":
            payload["id"] = "unrequested"
        case "naive_start":
            entry["startedAt"] = CHANGED.replace(tzinfo=None).isoformat()
        case "before_creation":
            entry["startedAt"] = (CREATED - timedelta(seconds=1)).isoformat()
        case "after_update":
            entry["startedAt"] = (UPDATED + timedelta(seconds=1)).isoformat()
        case "closed_after_current" | "reversed_closed":
            old = deepcopy(entry)
            old["startedAt"] = (
                CREATED.isoformat()
                if damage == "closed_after_current"
                else UPDATED.isoformat()
            )
            old["endedAt"] = (
                UPDATED.isoformat()
                if damage == "closed_after_current"
                else CREATED.isoformat()
            )
            history.insert(0, old)
    monkeypatch.setattr(server, "_tool_get_issue", lambda _arguments: payload)
    tracker = linear_over_fake_mcp(server)
    with pytest.raises(TrackerProtocolError):
        await tracker.read_issue_state_change(issue_key=ROOT)
    assert len(server.tool_calls("get_issue")) == 1


async def test_prior_closed_interval_does_not_supply_current_stamp(server, monkeypatch):
    payload = deepcopy(server.issues[ROOT].wire())
    payload["stateHistory"].insert(
        0,
        {
            "state": {"id": "old", "name": "Backlog", "type": "backlog"},
            "startedAt": CREATED.isoformat(),
            "endedAt": CHANGED.isoformat(),
        },
    )
    monkeypatch.setattr(server, "_tool_get_issue", lambda _arguments: payload)
    value = await linear_over_fake_mcp(server).read_issue_state_change(issue_key=ROOT)
    assert value.state_changed_at == CHANGED
    assert value.issue.state_name == "Todo"


@pytest.mark.parametrize("damage", ["identity", "body", "parent", "state"])
async def test_collector_refuses_detail_drift(tracker, monkeypatch, damage):
    original = tracker.read_issue_state_change

    async def drift(*, issue_key):
        result = await original(issue_key=issue_key)
        updates = {
            "identity": {"issue_key": "unrequested"},
            "body": {"body": "changed after listing"},
            "parent": {"parent_key": "new-parent"},
            "state": {"state_name": "different"},
        }
        return result.model_copy(
            update={"issue": result.issue.model_copy(update=updates[damage])}
        )

    monkeypatch.setattr(tracker, "read_issue_state_change", drift)
    with pytest.raises(ScopeReadError, match="changed before"):
        await collect_audit_candidates(tracker=tracker, scope=SCOPE)


@pytest.mark.parametrize("damage", ["add", "remove", "label"])
async def test_collector_rechecks_membership_after_history_reads(
    tracker, server, monkeypatch, damage
):
    original = tracker.read_issue_state_change
    changed = False

    async def drift(*, issue_key):
        nonlocal changed
        result = await original(issue_key=issue_key)
        if issue_key == CHILD and not changed:
            changed = True
            if isinstance(tracker, FakeTrackerPort):
                if damage == "add":
                    tracker.issues["added"] = result.issue.model_copy(
                        update={"issue_key": "added"}
                    )
                elif damage == "remove":
                    del tracker.issues[CHILD]
                else:
                    tracker.issues[CHILD] = result.issue.model_copy(
                        update={"issue_labels": frozenset()}
                    )
            elif damage == "add":
                server.issues["added"] = FakeMcpIssue(
                    id="added", parent_id=ROOT, labels=["acceptance-condition"]
                )
            elif damage == "remove":
                del server.issues[CHILD]
            else:
                server.issues[CHILD].labels = []
        return result

    monkeypatch.setattr(tracker, "read_issue_state_change", drift)
    with pytest.raises(ScopeReadError, match="during collection"):
        await collect_audit_candidates(tracker=tracker, scope=SCOPE)


async def test_collector_includes_criteria_missing_from_container_listing(
    tracker, monkeypatch
):
    async def only_parent(*, ref):
        assert ref == SCOPE
        return (await tracker.read_issue(issue_key=ROOT),)

    monkeypatch.setattr(tracker, "scope_issues", only_parent)
    result = await collect_audit_candidates(tracker=tracker, scope=SCOPE)
    assert {row.issue_key for row in result} == {ROOT, CHILD}


@pytest.mark.parametrize("surface", ["scope", "criteria"])
async def test_collector_refuses_duplicate_native_members(
    tracker, monkeypatch, surface
):
    if surface == "scope":
        original = tracker.scope_issues

        async def repeated(*, ref):
            rows = tuple(await original(ref=ref))
            return (*rows, rows[0])

        monkeypatch.setattr(tracker, "scope_issues", repeated)
    else:
        original = tracker.read_criteria

        async def repeated(*, issue_key):
            rows = tuple(await original(issue_key=issue_key))
            return (*rows, rows[0]) if rows else rows

        monkeypatch.setattr(tracker, "read_criteria", repeated)
    with pytest.raises(ScopeReadError, match="duplicate"):
        await collect_audit_candidates(tracker=tracker, scope=SCOPE)
