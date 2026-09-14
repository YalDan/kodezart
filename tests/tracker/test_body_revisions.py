"""Body-revision conformance runs over every registered tracker implementation."""

import pytest
from pydantic import ValidationError

from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.operation import LifecycleStage, QueueState
from kodezart.types.domain.tracker import TrackerIssueRevision
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import fixture_server, linear_over_fake_mcp

PARENT = "body/parent"
CRITERION = "body/condition"
ORIGINAL = "An exact body.\n\nUnicode: π\n"
REPLACEMENT = "A revised body.\n\nUnicode: λ\n"


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[PARENT] = FakeMcpIssue(id=PARENT, description=ORIGINAL)
    server.issues[CRITERION] = FakeMcpIssue(
        id=CRITERION,
        parent_id=PARENT,
        labels=["acceptance-condition"],
        description="**Check:** the exact behavior\n\n**Evidence:** —",
    )
    return server


@pytest.mark.parametrize("issue_key", [PARENT, CRITERION])
async def test_repeated_reads_preserve_body_and_digest_without_writes(
    tracker: TrackerPort, tracker_writes, issue_key
):
    writes = tracker_writes()
    first = await tracker.read_issue_revision(issue_key=issue_key)
    second = await tracker.read_issue_revision(issue_key=issue_key)
    assert first == second
    assert first.issue == await tracker.read_issue(issue_key=issue_key)
    assert first.body_digest.strip()
    assert tracker_writes() == writes
    if issue_key == CRITERION:
        assert first.issue.parent_key == PARENT
        assert first.issue.issue_labels == frozenset({"criterion"})


@pytest.mark.parametrize("issue_key", [PARENT, CRITERION])
@pytest.mark.parametrize("change", ["comment", "label", "workflow", "title"])
async def test_metadata_changes_preserve_the_body_revision(
    tracker: TrackerPort, issue_key, change
):
    before = await tracker.read_issue_revision(issue_key=issue_key)
    if change == "comment":
        await tracker.post_comment(issue_key=issue_key, body="A mention and reply.")
    elif change == "label":
        await tracker.set_queue_state(issue_key=issue_key, state=QueueState.PROPOSED)
    elif change == "workflow":
        await tracker.set_workflow_state(
            issue_key=issue_key, stage=LifecycleStage.IN_PROGRESS
        )
    else:
        await tracker.update_issue(issue_key=issue_key, title="A changed title")
    after = await tracker.read_issue_revision(issue_key=issue_key)
    assert after.issue.body == before.issue.body
    assert after.body_digest == before.body_digest


@pytest.mark.parametrize(
    "issue_key,other_key", [(PARENT, CRITERION), (CRITERION, PARENT)]
)
async def test_body_change_moves_only_its_surface_and_unchanged_replay_moves_none(
    tracker: TrackerPort, tracker_writes, issue_key, other_key
):
    before = await tracker.read_issue_revision(issue_key=issue_key)
    other = await tracker.read_issue_revision(issue_key=other_key)
    result = await tracker.edit_description(
        target=issue_key, expected=before.issue.body, replacement=REPLACEMENT
    )
    assert result is DescriptionEditResult.EDITED
    after = await tracker.read_issue_revision(issue_key=issue_key)
    assert after.issue.body == REPLACEMENT
    assert after.body_digest != before.body_digest
    assert await tracker.read_issue_revision(issue_key=other_key) == other
    writes = tracker_writes()
    replay = await tracker.edit_description(
        target=issue_key, expected=before.issue.body, replacement=REPLACEMENT
    )
    assert replay is DescriptionEditResult.UNCHANGED
    assert await tracker.read_issue_revision(issue_key=issue_key) == after
    assert await tracker.read_issue_revision(issue_key=other_key) == other
    assert tracker_writes() == writes


@pytest.mark.parametrize("body", ["", " ", "\n", "A\n", "A\r\n", "é", "é"])
async def test_every_exact_body_has_a_nonempty_stable_revision(tracker, body):
    await tracker.update_issue(issue_key=PARENT, body=body)
    before = await tracker.read_issue_revision(issue_key=PARENT)
    assert before.issue.body == body
    assert before.body_digest
    assert await tracker.read_issue_revision(issue_key=PARENT) == before
    await tracker.update_issue(issue_key=PARENT, body=body + " ")
    after = await tracker.read_issue_revision(issue_key=PARENT)
    assert after.body_digest != before.body_digest


@pytest.mark.parametrize("digest", [None, "", " \n"])
async def test_revision_rejects_an_unavailable_digest(tracker, digest):
    issue = await tracker.read_issue(issue_key=PARENT)
    with pytest.raises(ValidationError):
        TrackerIssueRevision(issue=issue, body_digest=digest)


async def test_revision_is_frozen_closed_and_does_not_normalize_opaque_digests(tracker):
    issue = await tracker.read_issue(issue_key=PARENT)
    snapshot = TrackerIssueRevision(issue=issue, body_digest=" revision:42 ")
    assert snapshot.body_digest == " revision:42 "
    with pytest.raises(ValidationError):
        snapshot.body_digest = "replacement"
    with pytest.raises(ValidationError):
        TrackerIssueRevision(issue=issue, body_digest="revision", vendor_time="now")
    with pytest.raises(ValidationError):
        TrackerIssueRevision(issue=issue)


async def test_linear_revision_uses_one_full_hydration_and_hashes_its_exact_body(
    server,
):
    tracker = linear_over_fake_mcp(server)
    server.issues[PARENT].description = ""
    before = len(server.calls)
    revision = await tracker.read_issue_revision(issue_key=PARENT)
    assert server.calls[before:] == [
        ("get_issue", {"id": PARENT, "includeRelations": True})
    ]
    assert revision.issue.body == ""
    assert revision.body_digest == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    server.issues[PARENT].description = ORIGINAL
    next_revision = await tracker.read_issue_revision(issue_key=PARENT)
    assert next_revision.issue.body == ORIGINAL
    assert next_revision.body_digest != revision.body_digest
    assert revision.issue.body == ""
