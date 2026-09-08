"""Native scope facts determine requests without a caller-supplied lane map."""

import asyncio

import pytest

from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.audit_requests import AuditRequestReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.audit import AuditClaimRequest
from kodezart.types.domain.audit_terminal import AuditTerminalRequest
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeTrackerPort
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_scope_reads import (
    EMPTY_INITIATIVE,
    EMPTY_PROJECT,
    INITIATIVE,
    MILESTONE,
    PROJECT,
)
from tests.tracker.test_scope_reads import (
    ROOT as CONTAINER_ROOT,
)
from tests.tracker.test_scope_reads import scope_fixture as scope_fixture
from tests.tracker.test_state_history import CHILD, CREATED, ROOT, SCOPE
from tests.tracker.test_state_history import server as server

REPO = "https://forge.invalid/team/repository"
OTHER_REPO = "https://forge.invalid/other/repository"
PREFIXES = {**MARKER_PREFIXES, "run_state": "audit-native"}


def operation(*, repos=(REPO,), bound=None):
    return OperationConfig.model_validate(
        {
            "operation_name": "fixture",
            "workspace": "fixture",
            "marker_prefixes": PREFIXES,
            "teams": {
                "engineering": {
                    "name": "fixture-team",
                    "key": "FIX",
                    "repository": bound,
                }
            },
            "repos": [{"url": repo, "trunk": "configured-trunk"} for repo in repos],
        }
    )


async def lane_record(tracker, *, lane="opaque:lane λ", owner=ROOT, data=None):
    record = LaneRunState.model_validate(
        {**record_data(), "laneKey": lane, **(data or {})}
    )
    stored = await tracker.post_comment(
        issue_key=owner,
        body=render_lane_record(record=record, marker_prefixes=PREFIXES),
    )
    return stored


async def route(tracker, repo):
    if isinstance(tracker, FakeTrackerPort):
        tracker.recorded_repositories[ROOT] = repo
    else:
        await tracker.post_comment(
            issue_key=ROOT, body=f'<!-- kodezart-repo url="{repo}" -->'
        )


@pytest.mark.parametrize(
    "lane", ["opaque:lane λ", "other/%/identity", "not-the-issue-key"]
)
async def test_native_scope_criteria_record_and_route_supply_the_requests(
    tracker, tracker_writes, lane
):
    stored = await lane_record(tracker, lane=lane)
    before = tracker_writes()
    reader = AuditRequestReader(tracker=tracker, operation=operation())
    snapshot = await reader.read(scope=SCOPE)
    assert [row.issue.issue_key for row in snapshot.targets] == [CHILD, ROOT]
    assert snapshot.candidates.candidates[0].state_changed_at == CREATED
    criterion, issue = snapshot.targets
    assert criterion.request == AuditClaimRequest(
        criterion_key=CHILD,
        lane_issue_key=ROOT,
        lane_key=lane,
        repo_url=REPO,
        record_ref=stored.comment_key,
    )
    assert issue.request == AuditTerminalRequest(
        issue_key=ROOT,
        lane_key=lane,
        repo_url=REPO,
        record_ref=stored.comment_key,
    )
    assert criterion.source == issue.source
    assert criterion.source.comment.body == stored.body
    assert criterion.source.record.head_sha == "head-full-identity"
    assert all(row.unavailable_reason is None for row in snapshot.targets)
    await reader.require_unchanged(snapshot)
    assert tracker_writes() == before


@pytest.mark.parametrize("state", list(WorkflowStateKind))
async def test_every_criterion_state_remains_in_the_native_census(
    tracker, server, state
):
    await lane_record(tracker)
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[CHILD] = tracker.issues[CHILD].model_copy(
            update={"state_name": f"state-{state.value}", "state_kind": state}
        )
    else:
        server.issues[CHILD].status = f"state-{state.value}"
        server.issues[CHILD].status_type = state.value
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=SCOPE
    )
    assert {row.issue.issue_key for row in snapshot.targets} == {CHILD, ROOT}
    assert snapshot.targets[0].issue.state_kind is state
    assert isinstance(snapshot.targets[0].request, AuditClaimRequest)


async def test_criterion_scope_reads_its_native_parent_outside_the_scope(tracker):
    stored = await lane_record(tracker)
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=CHILD)
    )
    assert [row.issue.issue_key for row in snapshot.targets] == [CHILD]
    assert snapshot.targets[0].request.record_ref == stored.comment_key
    assert snapshot.targets[0].source.issue.issue_key == ROOT


@pytest.mark.parametrize("mode", ["single", "explicit", "recorded"])
async def test_repository_selection_uses_existing_binding_and_recorded_route(
    tracker, mode
):
    await lane_record(tracker)
    if mode == "single":
        config = operation()
        await route(tracker, OTHER_REPO)
        expected = REPO
    elif mode == "explicit":
        config = operation(repos=(REPO, OTHER_REPO), bound=OTHER_REPO)
        await route(tracker, REPO)
        expected = OTHER_REPO
    else:
        config = operation(repos=(REPO, OTHER_REPO))
        await route(tracker, OTHER_REPO)
        expected = OTHER_REPO
    snapshot = await AuditRequestReader(tracker=tracker, operation=config).read(
        scope=SCOPE
    )
    assert {row.request.repo_url for row in snapshot.targets} == {expected}


@pytest.mark.parametrize("damage", ["missing", "foreign-route", "unknown-team"])
async def test_unknown_route_is_retained_as_unavailable_not_guessed(
    tracker, server, damage
):
    await lane_record(tracker)
    if damage == "foreign-route":
        await route(tracker, "https://unconfigured.invalid/repo")
    elif damage == "unknown-team":
        await route(tracker, REPO)
        if isinstance(tracker, FakeTrackerPort):
            tracker.issues[ROOT] = tracker.issues[ROOT].model_copy(
                update={"team_key": None}
            )
        else:
            server.issues[ROOT].team = "outside"
    snapshot = await AuditRequestReader(
        tracker=tracker, operation=operation(repos=(REPO, OTHER_REPO))
    ).read(scope=SCOPE)
    assert {row.issue.issue_key for row in snapshot.targets} == {CHILD, ROOT}
    assert all(
        row.request is None and row.unavailable_reason for row in snapshot.targets
    )


@pytest.mark.parametrize(
    "damage",
    ["missing", "duplicate", "other-lane", "malformed", "noncanonical", "reply"],
)
async def test_ambiguous_or_invalid_records_never_select_the_latest_valid_one(
    tracker, server, damage
):
    if damage != "missing":
        stored = await lane_record(tracker)
        if damage == "duplicate":
            await tracker.post_comment(issue_key=ROOT, body=stored.body)
        elif damage == "other-lane":
            await lane_record(tracker, lane="different-current-claim")
        elif damage in {"malformed", "noncanonical"}:
            replacement = (
                stored.body.replace('"filesChanged": 3', '"filesChanged": null')
                if damage == "malformed"
                else stored.body.replace("opaque%3Alane", "opaque%3alane")
            )
            if isinstance(tracker, FakeTrackerPort):
                tracker.comments[tracker.comments.index(stored)] = stored.model_copy(
                    update={"body": replacement}
                )
            else:
                next(
                    item for item in server.comments if item.id == stored.comment_key
                ).body = replacement
        else:
            if isinstance(tracker, FakeTrackerPort):
                tracker.comments[tracker.comments.index(stored)] = stored.model_copy(
                    update={"reply_to": "native-parent"}
                )
            else:
                next(
                    item for item in server.comments if item.id == stored.comment_key
                ).parent_id = "native-parent"
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=SCOPE
    )
    assert len(snapshot.targets) == 2
    assert all(
        row.request is None and row.unavailable_reason for row in snapshot.targets
    )


@pytest.mark.parametrize("change", ["body", "route", "membership"])
async def test_final_native_recheck_refuses_valid_source_changes(
    tracker, server, change
):
    stored = await lane_record(tracker)
    await route(tracker, REPO)
    reader = AuditRequestReader(
        tracker=tracker, operation=operation(repos=(REPO, OTHER_REPO))
    )
    snapshot = await reader.read(scope=SCOPE)
    assert all(row.request is not None for row in snapshot.targets)
    if change == "body":
        replacement = render_lane_record(
            record=LaneRunState.model_validate(
                {
                    **record_data(),
                    "laneKey": "opaque:lane λ",
                    "headSha": "changed-native-head",
                }
            ),
            marker_prefixes=PREFIXES,
        )
        if isinstance(tracker, FakeTrackerPort):
            tracker.comments[tracker.comments.index(stored)] = stored.model_copy(
                update={"body": replacement}
            )
        else:
            next(
                item for item in server.comments if item.id == stored.comment_key
            ).body = replacement
        assert "changed-native-head" in replacement
        _, decoded = await LaneRecordReader(
            tracker=tracker, operation=operation()
        ).read(issue_key=ROOT, lane_key="opaque:lane λ", record_ref=stored.comment_key)
        assert decoded.head_sha == "changed-native-head"
    elif change == "route":
        await route(tracker, OTHER_REPO)
    elif isinstance(tracker, FakeTrackerPort):
        tracker.issues[CHILD] = tracker.issues[CHILD].model_copy(
            update={"parent_key": "elsewhere"}
        )
    else:
        server.issues[CHILD].parent_id = "elsewhere"
    with pytest.raises(AuditClaimReadError, match="changed"):
        await reader.require_unchanged(snapshot)


async def test_cancellation_during_discovery_propagates(tracker, monkeypatch):
    async def cancel(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(tracker, "list_comments", cancel)
    with pytest.raises(asyncio.CancelledError):
        await AuditRequestReader(tracker=tracker, operation=operation()).read(
            scope=SCOPE
        )


@pytest.mark.parametrize(
    "scope,expected",
    [
        (INITIATIVE, {"FIX-1", "FIX-2", "FIX-3", "FIX-4", "FIX-5"}),
        (PROJECT, {"FIX-1", "FIX-2", "FIX-4"}),
        (MILESTONE, {"FIX-1", "FIX-2"}),
        (EMPTY_PROJECT, set()),
        (EMPTY_INITIATIVE, set()),
    ],
)
async def test_native_container_request_census_preserves_addressed_membership(
    scope_fixture, scope, expected
):
    tracker = scope_fixture.tracker
    await lane_record(tracker, owner=CONTAINER_ROOT.key)
    reader = AuditRequestReader(tracker=tracker, operation=operation())
    snapshot = await reader.read(scope=scope)
    assert {target.issue.issue_key for target in snapshot.targets} == expected
    if expected:
        root = next(
            item
            for item in snapshot.targets
            if item.issue.issue_key == CONTAINER_ROOT.key
        )
        assert root.request.lane_key == "opaque:lane λ"
    await reader.require_unchanged(snapshot)


@pytest.mark.parametrize(
    "neighbour",
    ["intro\n{body}", " {body}", "[audit-native-other:elsewhere]\nignored", ""],
)
async def test_lane_namespace_ignores_nonfirst_line_and_prefix_neighbours(
    tracker, tracker_writes, neighbour
):
    stored = await lane_record(tracker)
    await tracker.post_comment(issue_key=ROOT, body=neighbour.format(body=stored.body))
    before = tracker_writes()
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=SCOPE
    )
    assert all(row.request.record_ref == stored.comment_key for row in snapshot.targets)
    assert all(row.unavailable_reason is None for row in snapshot.targets)
    assert tracker_writes() == before


@pytest.mark.parametrize("ending", ["\r\n", "\r", "\u2028"])
async def test_lane_prefix_discovery_keeps_canonical_lf_record_identity(
    tracker, tracker_writes, ending
):
    record = LaneRunState.model_validate(record_data())
    body = render_lane_record(record=record, marker_prefixes=PREFIXES)
    marker, _, payload = body.partition("\n")
    await tracker.post_comment(issue_key=ROOT, body=f"{marker}{ending}{payload}")
    before = tracker_writes()
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=SCOPE
    )
    assert all(row.request is None for row in snapshot.targets)
    assert all(
        row.unavailable_reason
        == "AuditClaimReadError: the lane marker has no canonical identity"
        for row in snapshot.targets
    )
    assert tracker_writes() == before


@pytest.mark.parametrize("copies", [0, 2, 3])
async def test_missing_or_duplicate_lane_prefix_retains_native_refusal(
    tracker, tracker_writes, copies
):
    for _ in range(copies):
        await lane_record(tracker)
    before = tracker_writes()
    snapshot = await AuditRequestReader(tracker=tracker, operation=operation()).read(
        scope=SCOPE
    )
    assert all(row.request is None for row in snapshot.targets)
    assert all(
        row.unavailable_reason
        == "AuditClaimReadError: the issue has no unique native lane record"
        for row in snapshot.targets
    )
    assert tracker_writes() == before
