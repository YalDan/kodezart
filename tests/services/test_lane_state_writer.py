"""The lane's record is one comment the committing act keeps current."""

import pytest

from kodezart.domain.errors import LaneRecordWriteError, StaleCommentWriteError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_state import LaneBinding
from kodezart.types.domain.tracker import TrackerComment
from tests.fakes import FakeTrackerPort, PassThroughGate, make_tracker_issue
from tests.lane_fixture import LaneGit, LaneRepo, lane_operation

LANE = "LANE-1"
REMOTE = "origin"
REPO_URL = "https://forge.example/acme/repo"


def binding() -> LaneBinding:
    return LaneBinding(
        lane_key=LANE,
        loop_branch="ralph/LANE-1",
        deliverable_branch="feature/LANE-1",
        base_ref="trunk",
        repo_url=REPO_URL,
        repo_path=None,
        run_id="queue-job-1",
        visibility=RepoVisibility.PRIVATE,
    )


def board() -> FakeTrackerPort:
    operation = lane_operation()
    return FakeTrackerPort(
        issues=[make_tracker_issue(LANE)],
        marker_prefixes=operation.marker_prefixes,
    )


def writer(port: FakeTrackerPort, repo: LaneRepo, gate=None) -> TrackerLaneStateWriter:
    return TrackerLaneStateWriter(
        tracker=port,
        operation=lane_operation(),
        git=LaneGit(repo),
        git_remote=REMOTE,
        forge=None,
        gate=PassThroughGate() if gate is None else gate,
    )


async def make_commit(lane_state, repo: LaneRepo, index: int, *, publish: bool = True):
    """Commit, push, and record it the way the persist phase does."""
    sha = repo.commit()
    if publish:
        repo.publish()
    return await lane_state.record_commit(
        lane=binding(),
        workspace_path="/workspace/lane",
        receipt=PersistResult(
            commit_sha=sha,
            branch=binding().loop_branch,
            message=f"feat: commit {index}\n\nthe body of commit {index}",
            source=PersistSource.WORKING_TREE_COMMIT,
        ),
    )


def record_comments(port: FakeTrackerPort) -> list:
    prefix = lane_operation().marker_prefixes["run_state"]
    return [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}:")
    ]


async def test_the_tenth_commit_edits_the_one_record_and_posts_nothing():
    port, repo = board(), LaneRepo()
    lane_state = writer(port, repo)

    await make_commit(lane_state, repo, 1)
    first_key = record_comments(port)[0].comment_key

    for index in range(2, 10):
        await make_commit(lane_state, repo, index)
    comments_before = len(port.comments)
    events_before = await port.lane_run_events(issue_key=LANE, lane_key=LANE)

    tenth = await make_commit(lane_state, repo, 10)

    assert [comment.comment_key for comment in record_comments(port)] == [first_key]
    assert len(port.comments) == comments_before
    assert await port.lane_run_events(issue_key=LANE, lane_key=LANE) == events_before
    assert port.lease_writes == []

    _, stored = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    assert stored == tenth
    assert stored.head_sha == repo.head
    assert [row.sha for row in stored.commits] == repo.shas
    assert len(stored.commits) == 10
    assert [row.subject for row in stored.commits] == [
        f"feat: commit {index}" for index in range(1, 11)
    ]
    assert {row.issue_id for row in stored.commits} == {LANE}
    assert [item.role for item in stored.associations] == [
        BranchRole.DELIVERABLE,
        BranchRole.LOOP,
    ]


async def test_the_record_is_written_as_derived_content_on_a_comment():
    port, repo, gate = board(), LaneRepo(), PassThroughGate()
    await make_commit(writer(port, repo, gate), repo, 1)
    assert gate.content_classes == [ContentClass.DERIVED]
    assert gate.destinations == [OutboundDestination.TRACKER_COMMENT]


async def test_a_receipt_naming_another_commit_refuses_before_any_write():
    port, repo = board(), LaneRepo()
    repo.commit()
    repo.publish()
    with pytest.raises(LaneRecordWriteError, match="the receipt names"):
        await writer(port, repo).record_commit(
            lane=binding(),
            workspace_path="/workspace/lane",
            receipt=PersistResult(
                commit_sha="d" * 40,
                branch=binding().loop_branch,
                message="feat: another commit",
                source=PersistSource.WORKING_TREE_COMMIT,
            ),
        )
    assert port.comments == []


async def test_a_damaged_record_is_refused_and_never_overwritten():
    port, repo = board(), LaneRepo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    damaged = record_comments(port)[0]
    port.comments[port.comments.index(damaged)] = damaged.model_copy(
        update={"body": damaged.body.replace('"commitsAhead": 1', '"commitsAhead": []')}
    )
    with pytest.raises(LaneRecordWriteError, match="could not be read"):
        await make_commit(lane_state, repo, 2)
    assert record_comments(port)[0].body.count('"commitsAhead": []') == 1


class RewritingBoard(FakeTrackerPort):
    """A board whose record comment is edited between the read and the write."""

    async def upsert_comment(
        self,
        *,
        target: str,
        marker: str,
        body: str,
        holder: str | None = None,
        expected: TrackerComment | None = None,
    ) -> TrackerComment:
        for stored in record_comments(self):
            self.comments[self.comments.index(stored)] = stored.model_copy(
                update={"body": stored.body.replace('"filesChanged"', '"changed"')}
            )
        return await super().upsert_comment(
            target=target, marker=marker, body=body, holder=holder, expected=expected
        )


class AlteringGate(PassThroughGate):
    """A gate that returns a redacted body rather than the bytes it was given."""

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        await super().gate(
            content=content,
            visibility=visibility,
            shape=shape,
            destination=destination,
            content_class=content_class,
        )
        return GateDecision(
            verdict=GateVerdict.REDACTED, content=content.replace('"', "*", 1)
        )


async def test_a_record_altered_after_the_read_refuses_and_is_left_as_it_stands():
    port, repo = (
        RewritingBoard(
            issues=[make_tracker_issue(LANE)],
            marker_prefixes=lane_operation().marker_prefixes,
        ),
        LaneRepo(),
    )
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)

    with pytest.raises(StaleCommentWriteError):
        await make_commit(lane_state, repo, 2)

    body = record_comments(port)[0].body
    assert '"changed"' in body
    assert repo.shas[1] not in body


async def test_a_gate_that_alters_the_recorded_facts_refuses_the_whole_write():
    port, repo = board(), LaneRepo()
    with pytest.raises(LaneRecordWriteError, match="the outbound gate changed"):
        await make_commit(writer(port, repo, AlteringGate()), repo, 1)
    assert port.comments == []


async def stored_record(port: FakeTrackerPort):
    """The record as the board holds it, read back the way a cold lane reads it."""
    _, record = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    return record


async def test_pushed_head_is_absent_when_the_remote_read_returns_nothing():
    port, repo = board(), LaneRepo()
    record = await make_commit(writer(port, repo), repo, 1, publish=False)
    stored = await stored_record(port)
    assert repo.pushed is None
    assert stored == record
    assert stored.pushed_head_sha is None
    assert stored.head_sha == repo.head


async def test_pushed_head_equals_head_after_a_push():
    port, repo = board(), LaneRepo()
    record = await make_commit(writer(port, repo), repo, 1)
    stored = await stored_record(port)
    assert stored == record
    assert stored.pushed_head_sha == stored.head_sha == repo.head


async def test_pushed_head_behind_head_is_kept_as_its_own_value():
    port, repo = board(), LaneRepo()
    lane_state = writer(port, repo)
    pushed = await make_commit(lane_state, repo, 1)
    record = await make_commit(lane_state, repo, 2, publish=False)
    stored = await stored_record(port)
    assert stored == record
    assert stored.head_sha == repo.head
    assert stored.pushed_head_sha == pushed.head_sha
    assert stored.pushed_head_sha != stored.head_sha
    assert record.pushed_head_sha != record.head_sha
