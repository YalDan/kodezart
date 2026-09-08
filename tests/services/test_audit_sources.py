"""Native source reads preserve grading and branch identity without a session."""

from unittest.mock import AsyncMock

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.lane_records import LaneRecordReader
from tests.tracker import test_audit_evidence as fixtures
from tests.tracker.conftest import tracker as tracker
from tests.tracker.conftest import tracker_writes as tracker_writes

claim_setup = fixtures.claim_setup
setup = fixtures.setup
server = fixtures.server


def reader(setup, tracker):
    _, _, git, source, cache, *_ = setup
    return AuditSourceReader(
        tracker=tracker,
        records=LaneRecordReader(tracker=tracker, operation=fixtures.OPERATION),
        git=git,
        source=source,
        cache=cache,
        operation=fixtures.OPERATION,
        remote=AppConfig(git={"remote": "configured-remote"}).git.remote,
    )


async def test_actual_source_pair_reads_own_evidence_and_current_remote(
    setup, tracker, tracker_writes
):
    _, runner, git, source, cache, workspace, stored, _ = setup
    before = tracker_writes()
    value = await reader(setup, tracker).read(fixtures.REQUEST)
    assert value.criterion.issue_key == fixtures.CHILD
    assert value.check == fixtures.CHECK
    assert value.evidence.graded_sha == fixtures.PRIOR
    assert value.head_sha == fixtures.HEAD
    assert value.comment == stored
    assert value.repository == "/tmp/fake-cache"
    assert source.calls == [
        (value.repository, fixtures.PRIOR),
        (value.repository, fixtures.HEAD),
    ]
    assert ("is_ancestor", value.repository, fixtures.PRIOR, fixtures.HEAD) in git.calls
    assert cache.calls == [
        {"url": fixtures.REQUEST.repo_url, "cache_key": fixtures.REQUEST.cache_key}
    ]
    assert not runner.calls and not workspace.calls
    assert {call[2] for call in git.calls if call[0] == "remote_branch_sha"} == {
        "configured-remote"
    }
    assert tracker_writes() == before


@pytest.mark.parametrize("damage", ["criterion", "membership", "record", "head"])
async def test_reread_refuses_changed_source_identity(
    setup, tracker, monkeypatch, damage
):
    source = reader(setup, tracker)
    value = await source.read(fixtures.REQUEST)
    if damage == "criterion":
        await tracker.update_issue(
            issue_key=fixtures.CHILD, body=fixtures.body(fixtures.HEAD)
        )
    elif damage == "membership":
        original = tracker.read_criteria

        async def moved(**kwargs):
            rows = list(await original(**kwargs))
            rows[0] = rows[0].model_copy(update={"parent_key": "another-lane"})
            return rows

        monkeypatch.setattr(tracker, "read_criteria", moved)
    elif damage == "record":
        await tracker.upsert_comment(
            target=fixtures.ROOT,
            marker=value.comment.body.splitlines()[0],
            body="The original source record disappeared.",
        )
    else:
        monkeypatch.setattr(
            source._git, "remote_branch_sha", AsyncMock(return_value=fixtures.PRIOR)
        )
    with pytest.raises(AuditEvidenceReadError):
        await source.require_unchanged(value)


@pytest.mark.parametrize("damage", ["missing", "foreign", "not-commit"])
async def test_unavailable_or_unrelated_revision_never_becomes_a_snapshot(
    setup, tracker, monkeypatch, damage
):
    source = reader(setup, tracker)
    if damage == "missing":
        monkeypatch.setattr(
            source._git, "remote_branch_sha", AsyncMock(return_value=None)
        )
    elif damage == "foreign":
        source._git._ancestor_pairs.clear()
    else:
        source._source.changed = "c" * 40
    with pytest.raises(AuditEvidenceReadError):
        await source.read(fixtures.REQUEST)


@pytest.mark.parametrize("state", ["Todo", "Backlog", "In Progress", "Canceled"])
async def test_ineligible_state_refuses_before_repository_work(setup, tracker, state):
    source = reader(setup, tracker)
    await tracker.restore_workflow_state(issue_key=fixtures.CHILD, state_name=state)
    with pytest.raises(AuditEvidenceReadError):
        await source.read(fixtures.REQUEST)
    assert source._cache.calls == [] and source._git.calls == []


async def test_missing_evidence_is_never_a_current_head_default(setup, tracker):
    source = reader(setup, tracker)
    await tracker.update_issue(
        issue_key=fixtures.CHILD, body="**Check:** Check the behavior."
    )
    with pytest.raises(AuditEvidenceReadError):
        await source.read(fixtures.REQUEST)
    assert source._cache.calls == [] and source._git.calls == []


async def test_valid_same_comment_replacement_invalidates_the_original_snapshot(
    setup, tracker
):
    source = reader(setup, tracker)
    snapshot = await source.read(fixtures.REQUEST)
    changed = snapshot.record.model_copy(update={"files_changed": 17})
    marker, payload = render_lane_record(
        record=changed, marker_prefixes=fixtures.PREFIXES
    ).split("\n", 1)
    replacement = await tracker.upsert_comment(
        target=fixtures.ROOT, marker=marker, body=payload
    )
    assert replacement.comment_key == snapshot.comment.comment_key
    current, decoded = await source._records.read(
        issue_key=fixtures.ROOT,
        lane_key=fixtures.REQUEST.lane_key,
        record_ref=replacement.comment_key,
    )
    assert current == replacement and decoded.files_changed == 17
    assert decoded != snapshot.record
    with pytest.raises(AuditEvidenceReadError, match="lane record changed"):
        await source.require_unchanged(snapshot)
