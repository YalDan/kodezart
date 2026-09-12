"""The same persisted deliverable identity never creates a second issue."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import DuplicateIssueIdentityError, StaleWriteError
from kodezart.types.domain.issue_identity import IssueIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssuePriority, IssueQuery, TrackerIssue
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import FIXTURE_NOW, linear_over_fake_mcp

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scope-one")
DELIVERABLE = "deliverable-one"
BODY = "The user's description."


async def upsert(tracker: TrackerPort, **changes: object) -> TrackerIssue:
    values = {
        "scope_key": SCOPE,
        "deliverable_key": DELIVERABLE,
        "title": "Deliverable",
        "body": BODY,
        "team_key": "engineering",
        "priority": IssuePriority.HIGH,
        **changes,
    }
    return await tracker.upsert_issue(**values)


async def issue_count(tracker: TrackerPort) -> int:
    return len(await tracker.scan_issues(query=IssueQuery(page_size=100)))


@pytest.fixture
def reopened_tracker(tracker, server):
    def reopen():
        if isinstance(tracker, FakeTrackerPort):
            return FakeTrackerPort(
                issues=tuple(tracker.issues.values()),
                issue_identities=tracker.issue_identities,
                clock=lambda: FIXTURE_NOW,
            )
        return linear_over_fake_mcp(server)

    return reopen


@pytest.fixture
def duplicate_identity(tracker):
    async def duplicate(original: TrackerIssue):
        duplicate = await tracker.create_issue(
            title=original.title,
            body=original.body,
            team_key="engineering",
            priority=original.priority,
        )
        if isinstance(tracker, FakeTrackerPort):
            tracker.issue_identities[duplicate.issue_key] = tracker.issue_identities[
                original.issue_key
            ]
        return duplicate

    return duplicate


async def test_same_identity_twice_returns_one_issue_without_replay_writes(
    tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
):
    count = await issue_count(tracker)
    first = await upsert(tracker)
    calls = tracker_writes()
    second = await upsert(tracker)
    assert first.issue_key == second.issue_key
    assert first.model_dump_json() == second.model_dump_json()
    assert await issue_count(tracker) == count + 1
    assert tracker_writes() == calls
    assert await tracker.read_issue_identity(
        issue_key=first.issue_key
    ) == IssueIdentity(scope_key=SCOPE, deliverable_key=DELIVERABLE)


async def test_fresh_adapter_reads_persisted_identity_without_a_process_index(
    tracker: TrackerPort, reopened_tracker
):
    first = await upsert(tracker)
    reopened = reopened_tracker()
    count = await issue_count(reopened)
    second = await upsert(reopened)
    assert second.issue_key == first.issue_key
    assert await issue_count(reopened) == count
    assert await reopened.read_issue_identity(
        issue_key=first.issue_key
    ) == IssueIdentity(scope_key=SCOPE, deliverable_key=DELIVERABLE)


async def test_changed_hit_uses_guarded_description_edit_and_updates_title(
    tracker: TrackerPort, monkeypatch
):
    first = await upsert(tracker)
    guarded = AsyncMock(wraps=tracker.edit_description)
    monkeypatch.setattr(tracker, "edit_description", guarded)
    second = await upsert(tracker, title="Changed title", body="Changed description")
    assert second.issue_key == first.issue_key
    assert second.title == "Changed title"
    assert second.body.endswith("Changed description")
    assert guarded.await_count == 1
    arguments = guarded.await_args.kwargs
    assert arguments["target"] == first.issue_key
    assert arguments["expected"] == first.body
    assert await tracker.read_issue_identity(
        issue_key=second.issue_key
    ) == IssueIdentity(scope_key=SCOPE, deliverable_key=DELIVERABLE)


async def test_stale_description_refusal_prevents_the_following_title_write(
    tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]], monkeypatch
):
    first = await upsert(tracker)
    calls = tracker_writes()
    guarded = AsyncMock(
        side_effect=StaleWriteError(target=first.issue_key, expected=first.body)
    )
    monkeypatch.setattr(tracker, "edit_description", guarded)
    with pytest.raises(StaleWriteError):
        await upsert(tracker, title="Changed title", body="Changed description")
    assert await tracker.read_issue(issue_key=first.issue_key) == first
    assert tracker_writes() == calls


async def test_scope_kind_scope_key_and_deliverable_all_participate_in_identity(
    tracker: TrackerPort,
):
    issued = [await upsert(tracker)]
    issued.append(
        await upsert(tracker, scope_key=ScopeRef(kind=ScopeKind.ISSUE, key=SCOPE.key))
    )
    issued.append(
        await upsert(tracker, scope_key=ScopeRef(kind=SCOPE.kind, key="scope-two"))
    )
    issued.append(await upsert(tracker, deliverable_key="deliverable-two"))
    assert len({issue.issue_key for issue in issued}) == len(issued)


async def test_title_similarity_does_not_adopt_an_unkeyed_issue(tracker: TrackerPort):
    ordinary = await tracker.create_issue(
        title="Deliverable",
        body=BODY,
        team_key="engineering",
        priority=IssuePriority.HIGH,
    )
    assert await tracker.read_issue_identity(issue_key=ordinary.issue_key) is None
    keyed = await upsert(tracker)
    assert keyed.issue_key != ordinary.issue_key


async def test_duplicate_identities_refuse_without_picking_a_survivor(
    tracker: TrackerPort,
    tracker_writes: Callable[[], tuple[object, ...]],
    duplicate_identity,
):
    first = await upsert(tracker)
    second = await duplicate_identity(first)
    calls = tracker_writes()
    with pytest.raises(DuplicateIssueIdentityError) as raised:
        await upsert(tracker, body="Changed")
    assert raised.value.scope_key == SCOPE
    assert raised.value.deliverable_key == DELIVERABLE
    assert set(raised.value.issue_keys) == {first.issue_key, second.issue_key}
    assert tracker_writes() == calls


async def test_ordinary_description_writes_preserve_adapter_owned_identity(
    tracker: TrackerPort,
):
    first = await upsert(tracker)
    await tracker.update_issue(
        issue_key=first.issue_key, body="Replacement description"
    )
    assert await tracker.read_issue_identity(
        issue_key=first.issue_key
    ) == IssueIdentity(scope_key=SCOPE, deliverable_key=DELIVERABLE)
    replay = await upsert(tracker, body="Replacement description")
    assert replay.issue_key == first.issue_key


async def test_read_description_can_be_passed_back_without_duplicating_the_carrier(
    tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
):
    first = await upsert(tracker)
    calls = tracker_writes()
    second = await upsert(tracker, body=first.body)
    assert second == first
    assert tracker_writes() == calls


async def test_creation_metadata_is_not_reapplied_over_existing_tracker_values(
    tracker: TrackerPort,
):
    first = await upsert(tracker)
    second = await upsert(tracker, priority=IssuePriority.LOW, team_key="another-team")
    assert second.priority == first.priority
    assert second.team_key == first.team_key


async def test_empty_deliverable_key_refuses_before_writes(
    tracker: TrackerPort, tracker_writes: Callable[[], tuple[object, ...]]
):
    calls = tracker_writes()
    with pytest.raises(ValidationError):
        await upsert(tracker, deliverable_key="")
    assert tracker_writes() == calls


async def test_opaque_keys_round_trip_without_becoming_markup_or_extra_lines(
    tracker: TrackerPort,
):
    scope = ScopeRef(kind=ScopeKind.ISSUE, key='scope-->雪\u2028"quoted"')
    key = 'delivery:\n-->"雪\u2028next'
    first = await upsert(tracker, scope_key=scope, deliverable_key=key)
    assert await tracker.read_issue_identity(
        issue_key=first.issue_key
    ) == IssueIdentity(scope_key=scope, deliverable_key=key)
    assert (
        await upsert(tracker, scope_key=scope, deliverable_key=key)
    ).issue_key == first.issue_key


async def test_changed_upsert_replay_keeps_one_issue_and_performs_zero_second_writes(
    tracker, tracker_writes
):
    first = await upsert(tracker, body="body plus body")
    amended = await upsert(tracker, body="new body plus body", title="Revised")
    writes = tracker_writes()
    repeated = await upsert(tracker, body="new body plus body", title="Revised")
    assert first.issue_key == amended.issue_key == repeated.issue_key
    assert repeated == amended
    assert tracker_writes() == writes


async def test_upsert_stale_full_body_cannot_edit_a_matching_fragment(
    tracker, tracker_writes, monkeypatch
):
    original = await upsert(tracker)
    edit = tracker.edit_description
    foreign = None
    writes = None

    async def interleaved(**arguments):
        nonlocal foreign, writes
        foreign = await tracker.update_issue(
            issue_key=original.issue_key,
            body=original.body + "\nA principal added an independent clause.",
        )
        writes = tracker_writes()
        return await edit(**arguments)

    monkeypatch.setattr(tracker, "edit_description", interleaved)
    with pytest.raises(StaleWriteError):
        await upsert(tracker, body="A replacement", title="Must not be applied")
    assert await tracker.read_issue(issue_key=original.issue_key) == foreign
    assert tracker_writes() == writes
