"""The lane's record is one comment the committing act keeps current."""

import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
import structlog

from kodezart.adapters.outbound_admission import OutboundAdmission
from kodezart.adapters.reference_content_scanner import ReferenceContentScanner
from kodezart.domain.audit_claims import evidence_row_history, restamp_verdict
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.criteria_grading import NO_WITHDRAWALS
from kodezart.domain.criterion_cross_off import (
    cross_offs_for,
    evaluation_observation,
    lapse_observation,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    LaneRecordWriteError,
    OutboundContentBlockedError,
    StaleCommentWriteError,
    StaleWriteError,
    TransientAPIError,
)
from kodezart.domain.fire_spec import (
    criterion_field_bodies,
    criterion_ref,
    replace_criterion_fields,
)
from kodezart.domain.issue_tree import SubtreeClosure, index_issue_tree, open_criteria
from kodezart.domain.lane_record import (
    LANDING_ROW_SUBJECT,
    REENTRY_SECTION,
    RUN_STATE_PURPOSE,
)
from kodezart.domain.lapse import GradedState
from kodezart.domain.run_event_stream import (
    RUN_EVENT_PURPOSE,
    LaneRunEvent,
    lane_run_events,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.types.domain.agent import CriterionResult, NodeSessionStartedEvent
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import UndemonstratedReason
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    TrackerAggregate,
    WriterShape,
)
from kodezart.types.domain.node_session import NodeInvocation, NodeSessionKey
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
    RunKind,
)
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.privacy import PrivateSurface
from kodezart.types.domain.run_event import (
    EVIDENCE_ROW_WRITES,
    UNDEMONSTRATED_EVENT_KINDS,
    RunEventKind,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.run_state import LaneBinding, LanePR
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerComment, WorkflowStateKind
from tests.domain.test_lane_record import (
    EARLIER_REENTRY_IDS,
    EARLIER_REENTRY_SECTIONS,
)
from tests.fakes import FakeTrackerPort, PassThroughGate, make_tracker_issue
from tests.lane_fixture import LaneGit, LaneRepo, LosingBoard, lane_operation

LANE = "LANE-1"
#: The remote this lane's repository is on, named unlike the production
#: default: under that default the value the writer must take from its
#: configuration coincides with the one a hard-coded remote would use, and a
#: record read off a remote nobody configured would read as this lane's.
REMOTE = "fixture-remote"
REPO_URL = "https://forge.example/acme/repo"
#: The subject digest this lane's fire entered on, as its record pins it.
SUBJECT_DIGEST = "f" * 64


def binding() -> LaneBinding:
    return LaneBinding(
        lane_key=LANE,
        body_digest=SUBJECT_DIGEST,
        loop_branch="ralph/LANE-1",
        deliverable_branch="feature/LANE-1",
        base_ref="trunk",
        repo_url=REPO_URL,
        repo_path=None,
        run_id="queue-job-1",
        visibility=RepoVisibility.PRIVATE,
    )


def lane_repo() -> LaneRepo:
    """This lane's repository, on the branch and remote its record reads."""
    return LaneRepo(branch=binding().loop_branch, remote=REMOTE)


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


async def make_commit(
    lane_state,
    repo: LaneRepo,
    index: int,
    *,
    publish: bool = True,
    lane: LaneBinding | None = None,
):
    """Commit, push, and record it the way the persist phase does."""
    sha = repo.commit()
    if publish:
        repo.publish()
    return await lane_state.record_commit(
        lane=binding() if lane is None else lane,
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


PR = LanePR(url="https://forge.example/acme/repo/pull/17", number=17, state="open")


def comment_upserts(port: FakeTrackerPort) -> list[tuple[str, str | None]]:
    """Every comment write this board is asked to make, and what it names.

    A write that leaves the same bytes on the same comment is invisible in
    the board's own comment list, so "writes nothing" is counted at the call
    and not inferred from what the board holds afterwards. Each call is
    recorded with its marker and the comment it named as its precondition, so
    an in-place edit that overwrote whatever the board holds now is a
    different entry here than one that named the comment it read.
    """
    calls: list[tuple[str, str | None]] = []
    writing = port.upsert_comment

    async def counted(*, target, marker, body, holder=None, expected=None):
        calls.append((marker, None if expected is None else expected.comment_key))
        return await writing(
            target=target,
            marker=marker,
            body=body,
            holder=holder,
            expected=expected,
        )

    port.upsert_comment = counted
    return calls


RECORD_MARKER = compose_comment_marker(
    prefixes=lane_operation().marker_prefixes, purpose=RUN_STATE_PURPOSE, lane=LANE
)


async def test_the_pull_request_is_set_in_place_and_a_repeat_writes_nothing():
    """Where a delivery is retained is the record, edited where it stands.

    A lane with no record gets no first record composed out of a delivery:
    the write is skipped, no comment is minted, and a log line names the lane
    and the pull request, so the delivery completes rather than failing after
    its pull request was opened (KOD-705). The pull request is then set on
    the record the commit left, in the one comment that record lives in,
    naming that comment as the edit's precondition, and setting the same one
    again writes nothing at all — a second delivery of the same head is not
    a second write.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    upserts = comment_upserts(port)

    with structlog.testing.capture_logs() as logs:
        skipped = await lane_state.record_pull_request(
            lane_key=LANE, pr=PR, visibility=binding().visibility
        )
    assert skipped is None
    assert port.comments == []
    assert upserts == []
    assert [
        (entry["lane"], entry["pull_request"])
        for entry in logs
        if entry["event"] == "lane_pull_request_not_recorded"
    ] == [(LANE, PR.url)]

    committed = await make_commit(lane_state, repo, 1)
    recorded = record_comments(port)[0]
    before = [(comment.comment_key, comment.body) for comment in port.comments]
    # The counter is live before anything is asked of it: the commit's own
    # record write is on it, under the record marker and naming no prior
    # comment. A counter that answered "no write" for any input would say
    # nothing about the repeat below.
    assert upserts == [(RECORD_MARKER, None)]

    with structlog.testing.capture_logs() as logs:
        carried = await lane_state.record_pull_request(
            lane_key=LANE, pr=PR, visibility=binding().visibility
        )

    assert carried is not None
    assert carried.pr == PR
    assert [
        entry for entry in logs if entry["event"] == "lane_pull_request_not_recorded"
    ] == []
    # The edit named the comment the writer had just read, so a record another
    # writer changed in between is refused rather than overwritten.
    assert upserts[-1] == (RECORD_MARKER, recorded.comment_key)
    assert len(upserts) == 2
    # One comment, the same one, edited: the pull request is the only fact
    # that moved.
    assert [comment.comment_key for comment in record_comments(port)] == [
        recorded.comment_key
    ]
    assert len(port.comments) == len(before)
    _, stored = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    assert stored == carried
    # Everything else is what the commit recorded: the head, the rows and the
    # associations are not rewritten by a delivery.
    assert carried.model_copy(update={"pr": None}) == committed

    unchanged = [(comment.comment_key, comment.body) for comment in port.comments]
    written = list(upserts)
    again = await lane_state.record_pull_request(
        lane_key=LANE, pr=PR, visibility=binding().visibility
    )
    assert again == carried
    # Not a write that happened to leave the same bytes: no write was made.
    # The body is what an edit of the same comment would produce either way.
    assert upserts == written
    assert [
        (comment.comment_key, comment.body) for comment in port.comments
    ] == unchanged

    moved = await lane_state.record_pull_request(
        lane_key=LANE,
        pr=PR.model_copy(update={"state": "merged"}),
        visibility=binding().visibility,
    )
    assert moved.pr is not None and moved.pr.state == "merged"
    assert [comment.comment_key for comment in record_comments(port)] == [
        recorded.comment_key
    ]
    # A changed pull request is one more write, naming the record as it stands
    # after the write before it.
    assert upserts[-1] == (RECORD_MARKER, recorded.comment_key)
    assert len(upserts) == 3


async def test_a_landing_is_a_row_on_the_record_and_skipped_where_there_is_none():
    """The landing act rides on the lane's record, and composes none of its own.

    A lane with no record — a commit pushed whose record write then failed —
    has nothing to re-enter from, so the act has nothing to carry it: the
    write is skipped, no comment is minted, and a log line names the lane and
    the landed sha. Refusing instead would take the delivery after the
    landing down with it. Once the lane has its record, the same call
    appends the act as the record's newest row, edited in place (KOD-705).
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    upserts = comment_upserts(port)
    orphan = repo.commit()

    with structlog.testing.capture_logs() as logs:
        skipped = await lane_state.record_landing(
            lane=binding(), repo_path="/cache/lane", landed_sha=orphan
        )

    assert skipped is None
    assert port.comments == []
    assert upserts == []
    assert [
        (entry["lane"], entry["landed_sha"])
        for entry in logs
        if entry["event"] == "lane_landing_not_recorded"
    ] == [(LANE, orphan)]

    committed = await make_commit(lane_state, repo, 2)
    recorded = record_comments(port)[0]
    with structlog.testing.capture_logs() as logs:
        landed = await lane_state.record_landing(
            lane=binding(), repo_path="/cache/lane", landed_sha=orphan
        )

    assert landed is not None
    assert [row.sha for row in landed.commits] == [
        *(row.sha for row in committed.commits),
        orphan,
    ]
    assert landed.commits[-1].subject == LANDING_ROW_SUBJECT
    assert upserts[-1] == (RECORD_MARKER, recorded.comment_key)
    assert [comment.comment_key for comment in record_comments(port)] == [
        recorded.comment_key
    ]
    _, stored = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    assert stored == landed
    assert [
        entry for entry in logs if entry["event"] == "lane_landing_not_recorded"
    ] == []


@pytest.mark.parametrize("section", EARLIER_REENTRY_SECTIONS, ids=EARLIER_REENTRY_IDS)
async def test_the_next_write_of_an_earlier_record_renders_the_current_reentry(
    section,
):
    """A record read under an earlier re-entry text is migrated by its next write.

    The comment on the board ends with the section a writer rendered before it
    changed. The writer reads it as the lane's record, and the commit after it
    edits that same comment into the current section, so the earlier text
    goes away with the next act and not by hand.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    first = await make_commit(lane_state, repo, 1)
    stored = record_comments(port)[0]
    assert stored.body.endswith("\n\n" + REENTRY_SECTION)
    earlier = stored.model_copy(
        update={"body": stored.body.removesuffix(REENTRY_SECTION) + section}
    )
    port.comments[port.comments.index(stored)] = earlier
    _, read = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    assert read == first

    second = await make_commit(lane_state, repo, 2)

    [rewritten] = record_comments(port)
    assert rewritten.comment_key == stored.comment_key
    assert rewritten.body.endswith("\n\n" + REENTRY_SECTION)
    assert section not in rewritten.body
    assert [row.sha for row in second.commits] == repo.shas


async def test_the_tenth_commit_edits_the_one_record_and_posts_nothing():
    port, repo = board(), lane_repo()
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
    port, repo, gate = board(), lane_repo(), PassThroughGate()
    await make_commit(writer(port, repo, gate), repo, 1)
    assert gate.content_classes == [ContentClass.DERIVED]
    assert gate.destinations == [OutboundDestination.TRACKER_COMMENT]


async def test_a_receipt_naming_another_commit_refuses_before_any_write():
    port, repo = board(), lane_repo()
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
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    damaged = record_comments(port)[0]
    port.comments[port.comments.index(damaged)] = damaged.model_copy(
        update={"body": damaged.body.replace('"commitsAhead": 1', '"commitsAhead": []')}
    )
    with pytest.raises(LaneRecordWriteError, match="could not be read"):
        await make_commit(lane_state, repo, 2)
    assert record_comments(port)[0].body.count('"commitsAhead": []') == 1


async def test_a_record_marker_carried_by_a_reply_refuses_and_writes_nothing():
    """A threaded copy of the marker is not this lane's record.

    Read as absence it would be worse than unreadable: the next commit would
    compose a first record over a lane that already has one, so the reply is
    a refusal at the writer and not only at the reader.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    stored = record_comments(port)[0]
    port.comments[port.comments.index(stored)] = stored.model_copy(
        update={"reply_to": "discussion"}
    )
    before = [(comment.comment_key, comment.body) for comment in port.comments]

    with pytest.raises(LaneRecordWriteError, match="reply"):
        await make_commit(lane_state, repo, 2)

    assert [(comment.comment_key, comment.body) for comment in port.comments] == before


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
        aggregates: tuple[TrackerAggregate, ...],
    ) -> GateDecision:
        await super().gate(
            content=content,
            visibility=visibility,
            shape=shape,
            destination=destination,
            content_class=content_class,
            aggregates=aggregates,
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
        lane_repo(),
    )
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)

    with pytest.raises(StaleCommentWriteError):
        await make_commit(lane_state, repo, 2)

    body = record_comments(port)[0].body
    assert '"changed"' in body
    assert repo.shas[1] not in body


async def test_a_gate_that_alters_the_recorded_facts_refuses_the_whole_write():
    port, repo = board(), lane_repo()
    with pytest.raises(LaneRecordWriteError, match="the outbound gate changed"):
        await make_commit(writer(port, repo, AlteringGate()), repo, 1)
    assert port.comments == []


async def test_a_record_altered_after_the_read_refuses_the_pull_request_write():
    """The delivery's edit names the comment it read, so it overwrites nothing.

    A commit recorded between the delivery write's read and its write would
    otherwise be lost: the delivery would put its own stale record, without
    that commit's row, over the newer one.
    """
    port, repo = (
        RewritingBoard(
            issues=[make_tracker_issue(LANE)],
            marker_prefixes=lane_operation().marker_prefixes,
        ),
        lane_repo(),
    )
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    recorded = record_comments(port)[0]

    with pytest.raises(StaleCommentWriteError):
        await lane_state.record_pull_request(
            lane_key=LANE, pr=PR, visibility=binding().visibility
        )

    assert [comment.comment_key for comment in record_comments(port)] == [
        recorded.comment_key
    ]
    body = record_comments(port)[0].body
    assert '"changed"' in body
    assert PR.url not in body


async def test_a_duplicated_record_refuses_the_pull_request_write():
    """Two comments under the lane's marker are no basis for an in-place edit.

    Unreadable is not absent: a delivery that read a duplicated record as "no
    record" would refuse for the wrong reason, and one that picked either copy
    would edit a record it cannot show is this lane's.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    stored = record_comments(port)[0]
    port.comments.append(stored.model_copy(update={"comment_key": "second-copy"}))
    upserts = comment_upserts(port)

    with pytest.raises(LaneRecordWriteError, match="could not be read"):
        await lane_state.record_pull_request(
            lane_key=LANE, pr=PR, visibility=binding().visibility
        )

    assert upserts == []
    assert all(PR.url not in comment.body for comment in port.comments)


async def test_a_gate_that_alters_the_delivered_record_refuses_the_write():
    """The delivery write's bytes go through the same exact gate as the commit's.

    A redacted variant of a record is a different claim about where this lane
    stands, so the write refuses with the writer's own error and the record is
    left exactly as the commit wrote it.
    """
    port, repo = board(), lane_repo()
    await make_commit(writer(port, repo), repo, 1)
    before = [(comment.comment_key, comment.body) for comment in port.comments]

    with pytest.raises(LaneRecordWriteError, match="the outbound gate changed"):
        await writer(port, repo, AlteringGate()).record_pull_request(
            lane_key=LANE, pr=PR, visibility=binding().visibility
        )

    assert [(comment.comment_key, comment.body) for comment in port.comments] == before


class RecordingReferences(ReferenceContentScanner):
    """The production reference scan, keeping the bodies it was asked about."""

    def __init__(self, *, private_surface: PrivateSurface) -> None:
        super().__init__(private_surface=private_surface)
        self.scanned: list[str] = []

    async def scan(self, *, content: str, destination: OutboundDestination):
        self.scanned.append(content)
        return await super().scan(content=content, destination=destination)


class UnaskedJudgment:
    """A judgement a derived record write must never reach."""

    async def scan(self, *, content: str, destination: OutboundDestination):
        raise AssertionError("recorded lane facts are derived content, not prose")


async def test_both_writes_of_one_record_are_gated_under_the_lanes_own_visibility():
    """The delivery write asks the gate the question the commit write asked.

    Driven by the production admission and reference scan, over a deployment
    that declares this lane's forge host private. The record body carries that
    host twice — the branch page and, after a delivery, the pull request's own
    address — so a delivery write that asked the public question of a private
    lane would refuse the body its commit write had just admitted, after the
    pull request was opened and with nothing recording it (KOD-843).
    """
    references = RecordingReferences(
        private_surface=PrivateSurface(hosts=("forge.example",))
    )
    port, repo = board(), lane_repo()
    lane_state = writer(
        port, repo, OutboundAdmission(references=references, judgment=UnaskedJudgment())
    )

    committed = await make_commit(lane_state, repo, 1)
    # A private lane's record is admitted without a scan, and the body that was
    # admitted is the one carrying the private host.
    assert references.scanned == []
    assert REPO_URL in record_comments(port)[0].body
    assert committed.branch_url == REPO_URL

    # The same bytes, asked as a public lane's would be: scanned, and refused
    # for the host the deployment declared private.
    before = [(comment.comment_key, comment.body) for comment in port.comments]
    with pytest.raises(OutboundContentBlockedError):
        await lane_state.record_pull_request(
            lane_key=LANE, pr=PR, visibility=RepoVisibility.PUBLIC
        )
    assert [PR.url in body for body in references.scanned] == [True]
    assert [(comment.comment_key, comment.body) for comment in port.comments] == before

    carried = await lane_state.record_pull_request(
        lane_key=LANE, pr=PR, visibility=binding().visibility
    )

    assert carried.pr == PR
    assert PR.url in record_comments(port)[0].body
    # Still nothing scanned: this lane's visibility decided both writes.
    assert [PR.url in body for body in references.scanned] == [True]


def event_comments(port: FakeTrackerPort) -> list:
    prefix = lane_operation().marker_prefixes["run_event"]
    return [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}:")
    ]


async def test_an_event_lost_after_the_record_is_posted_by_the_next_commit():
    port = LosingBoard(
        issues=[make_tracker_issue(LANE)],
        marker_prefixes=lane_operation().marker_prefixes,
    )
    # The first-push event of the first commit is the write that is lost.
    port.lose("post_run_event")
    repo = lane_repo()
    lane_state = writer(port, repo)

    with pytest.raises(TransientAPIError):
        await make_commit(lane_state, repo, 1)
    assert await port.lane_run_events(issue_key=LANE, lane_key=LANE) == ()
    assert len(record_comments(port)) == 1

    await make_commit(lane_state, repo, 2)
    events = await port.lane_run_events(issue_key=LANE, lane_key=LANE)
    assert [event.kind for event in events] == [RunEventKind.FIRST_PUSH]

    await make_commit(lane_state, repo, 3)
    assert await port.lane_run_events(issue_key=LANE, lane_key=LANE) == events


async def a_commit(lane_state, repo: LaneRepo) -> None:
    """The act that records a commit and may post the first-push event."""
    await make_commit(lane_state, repo, 1)


async def an_undemonstrated_tick(lane_state, repo: LaneRepo) -> None:
    """The act that records a whole attempt whose readings all failed."""
    await tick(lane_state, sha="9" * 40, reasons=withheld())


@pytest.mark.parametrize(
    "damage",
    [
        lambda body: body.replace(RunEventKind.LANE_DISPATCHED.value, "invented"),
        lambda body: body.removesuffix("\n```"),
    ],
)
@pytest.mark.parametrize(
    "act",
    [
        pytest.param(a_commit, id="a-commit"),
        pytest.param(an_undemonstrated_tick, id="an-undemonstrated-tick"),
    ],
)
async def test_a_damaged_event_stream_refuses_the_write_instead_of_escaping(
    damage, act
):
    """The stream is read on the write path, so its faults are this write's.

    A parse failure here lands after the push, on every later commit of the
    lane's life; as the read error it is, it would reach the caller as a
    fault about nothing it can name, with a pushed commit behind it. The
    verdict act reads the same stream before it touches a sub-issue, so a
    damaged stream refuses there while every sub-issue still reads as
    whatever the last attempt left on it.
    """
    port, repo = criteria_board(), lane_repo()
    await port.post_run_event(
        issue_key=LANE,
        event=LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key=LANE),
    )
    stored = event_comments(port)[0]
    port.comments[port.comments.index(stored)] = stored.model_copy(
        update={"body": damage(stored.body)}
    )
    before = board_shape(port)

    with pytest.raises(LaneRecordWriteError, match="event stream could not be read"):
        await act(writer(port, repo), repo)

    assert record_comments(port) == []
    assert board_shape(port) == before


class CountingBoard(FakeTrackerPort):
    """A board counting the listings a write takes before it writes anything.

    Its own upsert lists the board again to find the comment it edits, so
    the listings that belong to the write itself are the ones it takes
    first: those are the readings its record and its first-push fact are
    composed from.
    """

    def __init__(self, **rest):
        super().__init__(**rest)
        self.listings = 0
        self.counting = True

    def count_the_next_write(self) -> None:
        """Start again, for the reads of the write that comes next."""
        self.listings, self.counting = 0, True

    async def list_comments(self, *, issue_key: str):
        if self.counting:
            self.listings += 1
        return await super().list_comments(issue_key=issue_key)

    async def upsert_comment(self, **rest):
        self.counting = False
        return await super().upsert_comment(**rest)


async def test_each_commit_reads_the_lanes_board_once_for_both_facts_it_needs():
    """The prior record and the state of the event stream are one reading.

    Listed twice, the write would pay a second round trip on every commit of
    the lane's life, and the record it composes would be built over a board
    the first-push fact was not read from.
    """
    port = CountingBoard(
        issues=[make_tracker_issue(LANE)],
        marker_prefixes=lane_operation().marker_prefixes,
    )
    repo = lane_repo()
    lane_state = writer(port, repo)

    await make_commit(lane_state, repo, 1)
    assert port.listings == 1

    port.count_the_next_write()
    await make_commit(lane_state, repo, 2)
    assert port.listings == 1


class UnvalidatableBoard(FakeTrackerPort):
    """A board whose listing holds something that is not a comment."""

    async def list_comments(self, *, issue_key: str):
        return [TrackerComment.model_validate({"body": "no identity at all"})]


async def test_a_listing_that_is_not_comments_refuses_the_write():
    """A validation failure on the read path carries no name a caller can act on.

    It arrives after the push, like the stream's own faults, so it is this
    write's refusal rather than a shape error about fields nobody asked for.
    """
    port, repo = (
        UnvalidatableBoard(
            issues=[make_tracker_issue(LANE)],
            marker_prefixes=lane_operation().marker_prefixes,
        ),
        lane_repo(),
    )
    with pytest.raises(LaneRecordWriteError, match="comments could not be read"):
        await make_commit(writer(port, repo), repo, 1)
    assert port.comments == []


async def test_an_event_of_another_kind_does_not_stand_in_for_the_first_push():
    """Only a first-push event says the lane has reached the remote.

    A lane is dispatched, and its nodes open sessions, before it ever
    pushes: read as "any event at all", those would answer for the one
    event this write exists to post, and the lane would never announce it.
    """
    port, repo = board(), lane_repo()
    await port.post_run_event(
        issue_key=LANE,
        event=LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key=LANE),
    )

    await make_commit(writer(port, repo), repo, 1)

    events = await port.lane_run_events(issue_key=LANE, lane_key=LANE)
    assert [event.kind for event in events] == [
        RunEventKind.LANE_DISPATCHED,
        RunEventKind.FIRST_PUSH,
    ]


async def posts_a_first_push(repo: LaneRepo):
    """The lane's first push, and the whole payload that event carries."""
    port = board()
    await make_commit(writer(port, repo), repo, 1)
    return port, {
        "kind": RunEventKind.FIRST_PUSH.value,
        "laneKey": LANE,
        "subjectKey": None,
        "gradedSha": None,
    }


async def posts_an_unverified_grading(repo: LaneRepo):
    """One criterion whose reading failed, and the whole payload it carries."""
    port = criteria_board()
    withdrawn = CRITERIA[:1]
    await tick(
        writer(port, repo),
        sha="9" * 40,
        keys=withdrawn,
        reasons=withheld(withdrawn),
    )
    return port, {
        "kind": RunEventKind.CRITERION_GRADING_UNVERIFIED.value,
        "laneKey": LANE,
        "subjectKey": withdrawn[0],
        "gradedSha": "9" * 40,
    }


@pytest.mark.parametrize(
    "posting",
    [
        pytest.param(posts_a_first_push, id="first-push"),
        pytest.param(posts_an_unverified_grading, id="grading-unverified"),
    ],
)
async def test_the_posted_event_carries_the_marker_and_the_codec_fields_alone(posting):
    repo = lane_repo()
    port, expected = await posting(repo)

    prefix = lane_operation().marker_prefixes["run_event"]
    marker, _, block = event_comments(port)[0].body.partition("\n")
    assert marker == f"[{prefix}:{LANE}]"
    assert block.startswith("```json\n")
    assert block.endswith("\n```")
    payload = json.loads(block[len("```json\n") : -len("\n```")])
    assert payload == expected
    assert set(payload) == {
        field.alias or name for name, field in LaneRunEvent.model_fields.items()
    }


@pytest.mark.parametrize("missing", [RUN_STATE_PURPOSE, RUN_EVENT_PURPOSE])
async def test_an_operation_missing_a_record_purpose_refuses_before_any_read(missing):
    """Both purposes this write needs are resolved together, or neither is.

    The record marker and the event prefix are written in the same act, so
    an operation missing either one is a refusal the caller is owed before
    the push; resolving only the first would leave the second absence to be
    found after a commit had reached the remote.
    """
    port, repo = board(), lane_repo()
    git = LaneGit(repo)
    lane_state = TrackerLaneStateWriter(
        tracker=port,
        operation=OperationConfig(
            operation_name="lane-fixture",
            workspace="fixture",
            marker_prefixes={
                purpose: prefix
                for purpose, prefix in lane_operation().marker_prefixes.items()
                if purpose != missing
            },
            issue_labels={"decision": "decision"},
        ),
        git=git,
        git_remote=REMOTE,
        forge=None,
        gate=PassThroughGate(),
    )
    with pytest.raises(OperationMemberAbsentError, match=missing):
        lane_state.require_writable(lane=binding())
    with pytest.raises(OperationMemberAbsentError, match=missing):
        await make_commit(lane_state, repo, 1)
    assert git.calls == []
    assert port.comments == []


async def test_a_lane_naming_no_repository_refuses_before_any_read():
    port, repo = board(), lane_repo()
    git = LaneGit(repo)
    lane_state = TrackerLaneStateWriter(
        tracker=port,
        operation=lane_operation(),
        git=git,
        git_remote=REMOTE,
        forge=None,
        gate=PassThroughGate(),
    )
    homeless = LaneBinding(
        lane_key=LANE,
        body_digest=SUBJECT_DIGEST,
        loop_branch="ralph/LANE-1",
        deliverable_branch="feature/LANE-1",
        base_ref="trunk",
        repo_url=None,
        repo_path=None,
        run_id="queue-job-1",
        visibility=RepoVisibility.PRIVATE,
    )
    with pytest.raises(LaneRecordWriteError, match="names no repository"):
        lane_state.require_writable(lane=homeless)
    # The same binding through the write itself: the address is resolved
    # among the first statements, so the refusal costs neither a git read
    # nor a tracker write rather than arriving after both.
    with pytest.raises(LaneRecordWriteError, match="names no repository"):
        await make_commit(lane_state, repo, 1, lane=homeless)
    assert git.calls == []
    assert port.comments == []


async def test_a_run_rebound_to_another_deliverable_leaves_the_board_untouched():
    """The refusal reaches the board's writer, not only the model.

    It is knowable only from the stored record, so it necessarily follows the
    push; what it must not follow is a write. The record that stands is the
    one the run was bound to, and the rebound commit adds nothing to it.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    before = [(comment.comment_key, comment.body) for comment in port.comments]
    rebound = LaneBinding(
        lane_key=LANE,
        body_digest=SUBJECT_DIGEST,
        loop_branch=binding().loop_branch,
        deliverable_branch="feature/another",
        base_ref=binding().base_ref,
        repo_url=REPO_URL,
        repo_path=None,
        run_id=binding().run_id,
        visibility=RepoVisibility.PRIVATE,
    )

    with pytest.raises(LaneRecordWriteError, match="feature/another"):
        await make_commit(lane_state, repo, 2, lane=rebound)

    assert [(comment.comment_key, comment.body) for comment in port.comments] == before
    events = await port.lane_run_events(issue_key=LANE, lane_key=LANE)
    assert [event.kind for event in events] == [RunEventKind.FIRST_PUSH]


async def stored_record(port: FakeTrackerPort):
    """The record as the board holds it, read back the way a cold lane reads it."""
    _, record = await LaneRecordReader(tracker=port, operation=lane_operation()).read(
        issue_key=LANE, lane_key=LANE
    )
    return record


async def test_pushed_head_is_absent_when_the_remote_read_returns_nothing():
    port, repo = board(), lane_repo()
    record = await make_commit(writer(port, repo), repo, 1, publish=False)
    stored = await stored_record(port)
    assert repo.pushed is None
    assert stored == record
    assert stored.pushed_head_sha is None
    assert stored.head_sha == repo.head


async def test_pushed_head_equals_head_after_a_push():
    port, repo = board(), lane_repo()
    record = await make_commit(writer(port, repo), repo, 1)
    stored = await stored_record(port)
    assert stored == record
    assert stored.pushed_head_sha == stored.head_sha == repo.head


async def test_pushed_head_returns_to_absent_when_the_remote_branch_is_gone():
    """The remote is observed at each commit, never carried from the last one.

    A record that kept its first push would still claim a remote copy after
    the branch was deleted from the remote, and the re-entry section would
    send a reader to check out a branch that is no longer there.
    """
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    await make_commit(lane_state, repo, 1)
    repo.pushed = None

    record = await make_commit(lane_state, repo, 2, publish=False)

    stored = await stored_record(port)
    assert stored == record
    assert stored.pushed_head_sha is None
    assert stored.head_sha == repo.head


async def test_pushed_head_behind_head_is_kept_as_its_own_value():
    port, repo = board(), lane_repo()
    lane_state = writer(port, repo)
    pushed = await make_commit(lane_state, repo, 1)
    record = await make_commit(lane_state, repo, 2, publish=False)
    stored = await stored_record(port)
    assert stored == record
    assert stored.head_sha == repo.head
    assert stored.pushed_head_sha == pushed.head_sha
    assert stored.pushed_head_sha != stored.head_sha
    assert record.pushed_head_sha != record.head_sha


# ---------------------------------------------------------------------------
# The tick: one criterion's state and the sha it was graded at, in one act.
# ---------------------------------------------------------------------------

CRITERIA = (f"{LANE}/first", f"{LANE}/second", f"{LANE}/third")


def check_of(key: str) -> str:
    return f"the check {key} states"


def criterion_body(key: str) -> str:
    return f"**Check:** {check_of(key)}\n**Do:** the build {key} names\n**Evidence:** —"


CHILD = f"{LANE}/child"
#: A criterion sub-issue of the deliverable child, below the fire's own family.
CHILD_CRITERION = f"{CHILD}/criterion"
#: A deliverable child of that child, and the criterion it owns: two
#: containers below the fire.
GRANDCHILD = f"{CHILD}/grandchild"
GRANDCHILD_CRITERION = f"{GRANDCHILD}/criterion"


def criteria_board(
    *,
    bodies: dict[str, str] | None = None,
    keys: Sequence[str] = CRITERIA,
    depth: int = 0,
) -> FakeTrackerPort:
    """The lane, its criterion sub-issues, and one issue that is not a criterion.

    At *depth* one, that child owns a criterion sub-issue of its own, so the
    board carries a subtree below the fire's direct family; at depth two, the
    child also has a deliverable child of its own that owns one criterion.
    """
    overrides = bodies or {}
    below = [
        make_tracker_issue(
            criterion,
            parent_key=container,
            issue_labels=frozenset({"criterion"}),
            body=criterion_body(criterion),
        )
        for container, criterion in (
            (CHILD, CHILD_CRITERION),
            (GRANDCHILD, GRANDCHILD_CRITERION),
        )[:depth]
    ]
    if depth >= 2:
        below.append(
            make_tracker_issue(GRANDCHILD, parent_key=CHILD, body="a plain grandchild")
        )
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, body="the lane's own text"),
            make_tracker_issue(CHILD, parent_key=LANE, body="a plain child"),
            *(
                make_tracker_issue(
                    key,
                    parent_key=LANE,
                    issue_labels=frozenset({"criterion"}),
                    body=overrides.get(key, criterion_body(key)),
                )
                for key in keys
            ),
            *below,
        ],
        marker_prefixes=lane_operation().marker_prefixes,
    )


def counting_criteria_board() -> CountingBoard:
    """The tick fixture's own board, counting the listings a write takes."""
    source = criteria_board()
    return CountingBoard(
        issues=list(source.issues.values()),
        marker_prefixes=lane_operation().marker_prefixes,
    )


def dispatched(keys: Sequence[str] = CRITERIA) -> tuple[TrackerCriterion, ...]:
    return tuple(
        TrackerCriterion(id=CriterionId(key), text=check_of(key)) for key in keys
    )


def graded(
    keys: Sequence[str] = CRITERIA, *, failed: Sequence[str] = ()
) -> tuple[CriterionResult, ...]:
    return tuple(
        CriterionResult(
            criterion_id=CriterionId(key),
            criterion=check_of(key),
            passed=key not in failed,
            reasoning="Observed the selected check.",
        )
        for key in keys
    )


def withheld(
    keys: Sequence[str] = CRITERIA,
    reason: UndemonstratedReason = UndemonstratedReason.workspace_not_the_graded_sha,
) -> dict[CriterionId, UndemonstratedReason]:
    """The reading that failed, named for each criterion it failed for."""
    return {CriterionId(key): reason for key in keys}


async def tick(
    lane_state,
    *,
    sha: str,
    keys: Sequence[str] = CRITERIA,
    failed: Sequence[str] = (),
    reasons: Mapping[CriterionId, UndemonstratedReason] = NO_WITHDRAWALS,
    lane: LaneBinding | None = None,
) -> None:
    """One attempt's whole verdict, written the way the evaluator writes it.

    *reasons* names the reading that failed for each criterion it failed for:
    which readings stand is the evaluator's fold, and the writer's tests are
    about what it does with the cross-offs that fold produced.
    """
    results = graded(keys, failed=failed)
    await lane_state.write_cross_offs(
        lane=binding() if lane is None else lane,
        dispatched=dispatched(keys),
        cross_offs=cross_offs_for(
            results=results,
            graded_sha=sha,
            observation=evaluation_observation(session_id="eval-session", iteration=1),
            reasons=reasons,
        ),
    )


def without_evidence(body: str) -> str:
    """The body with its Evidence field blanked: every other byte of it."""
    return replace_criterion_fields(body, replacements={"Evidence": ""})


async def test_a_full_lane_of_ticks_leaves_state_and_full_sha_on_every_sub_issue():
    """Three heads, three whole-lane verdicts, one value carrying both halves.

    After every round each addressed sub-issue is Done and its Evidence row
    carries that round's complete forty-hex sha; consecutive bodies of one
    sub-issue differ inside that row and nowhere else, so nothing but the
    state and the graded sha moved.
    """
    port = criteria_board()
    gate = PassThroughGate()
    lane_state = writer(port, lane_repo(), gate)
    heads = [format(index, "040x") for index in (1, 2, 3)]
    bodies: dict[str, list[str]] = {key: [] for key in CRITERIA}

    for sha in heads:
        await tick(lane_state, sha=sha)
        for key in CRITERIA:
            issue = port.issues[key]
            assert issue.state_kind is WorkflowStateKind.COMPLETED
            assert parse_criterion_evidence(issue.body).graded_sha == sha
            assert len(parse_criterion_evidence(issue.body).graded_sha) == 40
            bodies[key].append(issue.body)

    for key, written in bodies.items():
        assert len(written) == 3
        assert len(set(written)) == 3
        assert len({without_evidence(body) for body in written}) == 1
        assert without_evidence(written[0]) == without_evidence(criterion_body(key))
    # The first round moved every sub-issue; the two re-grades restamped the
    # sha without moving a state that was already Done.
    assert port.workflow_writes == [(key, LifecycleStage.DONE) for key in CRITERIA]
    # The Evidence row is the one derived write of a durable surface in the
    # source, and it declares no tracker aggregate: a graded sha and a test
    # name are neither a tracker count nor a list of tracker identities. So
    # the cheap path survives at a real durable writer.
    assert gate.destinations[-1] is OutboundDestination.TRACKER_DESCRIPTION
    assert gate.content_classes[-1] is ContentClass.DERIVED
    assert gate.aggregates[-1] == ()
    assert set(gate.aggregates) == {()}


async def test_a_verdict_that_does_not_answer_the_dispatched_roster_writes_nothing():
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    partial = graded(CRITERIA[:2])

    with pytest.raises(LaneRecordWriteError, match="dispatched criteria"):
        await lane_state.write_cross_offs(
            lane=binding(),
            dispatched=dispatched(),
            cross_offs=cross_offs_for(
                results=partial,
                graded_sha="4" * 40,
                observation="evaluator session eval-session, iteration 1",
                reasons={},
            ),
        )

    assert port.issue_writes == []
    assert port.workflow_writes == []


def board_shape(port: FakeTrackerPort) -> dict[str, tuple[str, str]]:
    """Every issue on the board as the two facts a tick could move."""
    return {
        key: (issue.body, issue.state_name)
        for key, issue in sorted(port.issues.items())
    }


async def test_a_tick_rewrites_only_the_addressed_sub_issue():
    """One verdict, one sub-issue: nothing else on the board is touched.

    The board is read whole before and after, so the assertion covers the
    lane's own issue, the sub-issues the same attempt graded and the child
    that is not a criterion at all, rather than the one issue a narrower
    check would have looked at.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    addressed = CRITERIA[1]
    before = board_shape(port)

    await tick(
        lane_state,
        sha="7" * 40,
        keys=CRITERIA,
        failed=[key for key in CRITERIA if key != addressed],
    )

    after = board_shape(port)
    assert {key for key in after if after[key] != before[key]} == {addressed}
    assert after[addressed][1] == LifecycleStage.DONE.value
    # The whole row, not the sha alone: the pointer back to the grading is
    # the second datum this write puts there, and a stamp carrying the sha
    # under any other pointer is not the grading just made.
    assert parse_criterion_evidence(port.issues[addressed].body) == CriterionEvidence(
        graded_sha="7" * 40,
        test=evaluation_observation(session_id="eval-session", iteration=1),
    )
    assert [key for key, _, _ in port.issue_writes] == [addressed]
    assert port.workflow_writes == [(addressed, LifecycleStage.DONE)]


async def test_a_lane_of_ticks_leaves_the_owning_body_byte_identical():
    """Satisfaction is written on the criteria, never on what owns them.

    Not even the rollup: the lane's own body and state are what a parent
    write would move, and after every criterion under it is Done both are
    the bytes the board started with.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    owning = board_shape(port)[LANE]

    for index in (1, 2, 3):
        await tick(lane_state, sha=format(index, "040x"))

    assert board_shape(port)[LANE] == owning
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in CRITERIA
    )
    assert LANE not in {key for key, _, _ in port.issue_writes}
    assert LANE not in {key for key, _ in port.workflow_writes}


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize(
    "drift",
    [
        pytest.param(
            {"state_kind": WorkflowStateKind.STARTED, "state_name": "In Progress"},
            id="state-moved",
        ),
        pytest.param(
            {"body": "**Check:** an amended Check\n**Evidence:** —"}, id="check-amended"
        ),
        pytest.param({"issue_labels": frozenset()}, id="classification-lost"),
    ],
)
async def test_a_tick_on_a_sub_issue_that_moved_after_dispatch_writes_nothing(
    drift, index
):
    """The sub-issue is read back through the port, never remembered.

    The verdict was reached against what the dispatch saw; the write asserts
    what the sub-issue holds now, and a sub-issue that moved in between
    takes no part of the write at all — not its body, not its state, and not
    the sub-issues the same attempt would have gone on to address. What
    PRECEDES it in the roster is already written when the refusal is raised:
    the act is per criterion, so the drifted position decides how much of
    the attempt landed.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    moved = CRITERIA[index]
    port.issues[moved] = port.issues[moved].model_copy(update=drift)
    before = board_shape(port)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="8" * 40)

    assert caught.value.target == moved
    written = list(CRITERIA[:index])
    assert [key for key, _, _ in port.issue_writes] == written
    assert port.workflow_writes == [(key, LifecycleStage.DONE) for key in written]
    after = board_shape(port)
    assert {key for key in after if after[key] != before[key]} == set(written)


class DriftingBoard(FakeTrackerPort):
    """A board that moves the NEXT criterion while this one is being written.

    The drift lands between the attempt's own reads, which is the window a
    writer that read every dispatched sub-issue once at the start cannot
    see: its snapshots were all taken before this move happened.
    """

    def __init__(self, *, moves: str, when: str) -> None:
        source = criteria_board()
        super().__init__(
            issues=list(source.issues.values()),
            marker_prefixes=lane_operation().marker_prefixes,
        )
        self._moves, self._when = moves, when

    async def set_workflow_state(self, *, issue_key, stage):
        if issue_key == self._when:
            self.issues[self._moves] = self.issues[self._moves].model_copy(
                update={
                    "state_kind": WorkflowStateKind.STARTED,
                    "state_name": "In Progress",
                }
            )
        return await super().set_workflow_state(issue_key=issue_key, stage=stage)


async def test_a_sub_issue_that_moves_mid_attempt_is_read_again_before_its_write():
    """Each write reads its own sub-issue, at the instant it writes it.

    The board moves the second criterion while the first is being finished,
    so what the attempt saw when it was dispatched and what the second
    sub-issue holds when its turn comes are different boards. The first
    criterion is finished at the graded sha, the second is refused, and the
    third — which the attempt never reached — is untouched.
    """
    port = DriftingBoard(moves=CRITERIA[1], when=CRITERIA[0])
    lane_state = writer(port, lane_repo())
    untouched = board_shape(port)[CRITERIA[2]]

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="a" * 40)

    assert caught.value.target == CRITERIA[1]
    assert port.issues[CRITERIA[0]].state_kind is WorkflowStateKind.COMPLETED
    assert (
        parse_criterion_evidence(port.issues[CRITERIA[0]].body).graded_sha == "a" * 40
    )
    assert board_shape(port)[CRITERIA[2]] == untouched
    assert [key for key, _, _ in port.issue_writes] == [CRITERIA[0]]
    assert port.workflow_writes == [(CRITERIA[0], LifecycleStage.DONE)]


class EditingBoard(FakeTrackerPort):
    """A board that changes the addressed sub-issue inside its own body edit.

    The change lands after the fresh read the write was decided on and
    before the edit it was decided for, which is the window the two halves
    of a tick sit in: whatever a writer remembered from before this moment
    is no longer what the sub-issue holds. The first *after* edits of the
    addressed sub-issue land untouched, so a test can finish it before the
    edit the change rides on.
    """

    def __init__(
        self, *, target: str, change: dict[str, object], after: int = 0
    ) -> None:
        source = criteria_board()
        super().__init__(
            issues=list(source.issues.values()),
            marker_prefixes=lane_operation().marker_prefixes,
        )
        self._target, self._change, self._after = target, change, after

    async def edit_description(self, *, target, expected, replacement, **rest):
        if target == self._target and self._after:
            self._after -= 1
        elif target == self._target:
            self.issues[target] = self.issues[target].model_copy(update=self._change)
        return await super().edit_description(
            target=target, expected=expected, replacement=replacement, **rest
        )


async def test_a_body_that_changed_under_the_stamp_leaves_the_criterion_unfinished():
    """The transition never rides on a body write that did not land.

    The board rewrites the sub-issue's body between the read the stamp was
    decided on and the edit itself, so the compare-and-set refuses. The
    criterion is left exactly as it was: no Evidence row of this grading and
    no move into the finished state, rather than finished with the row of
    the grading that never reached it.
    """
    port = EditingBoard(
        target=CRITERIA[0],
        change={"body": f"{criterion_body(CRITERIA[0])}\nedited while it was read"},
    )
    lane_state = writer(port, lane_repo())

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="c" * 40)

    assert caught.value.target == CRITERIA[0]
    assert port.issues[CRITERIA[0]].state_kind is WorkflowStateKind.UNSTARTED
    assert port.workflow_writes == []
    assert [key for key, _, _ in port.issue_writes] == []
    # The entry records the row write, so a stamp that never landed has none.
    assert stream(port) == []


#: The sub-issues a board moves while the stamp lands: the commit an earlier
#: attempt finished it at, if any, the commit this grading reads, and the
#: state the board takes it to.
MOVED_UNDER_THE_STAMP = {
    "unstarted": (None, "d" * 40, "In Progress"),
    "finished": ("1" * 40, "2" * 40, "In Review"),
}


@pytest.mark.parametrize("moved", sorted(MOVED_UNDER_THE_STAMP))
async def test_a_state_that_moved_under_the_stamp_is_not_moved_to_done(moved):
    """The transition reads its own sub-issue, because it carries no precondition.

    The board moves the sub-issue out of the states a tick addresses while
    the stamp is landing. The stamp is that grading's own record and stays,
    but the criterion is not finished on top of a board that took it
    somewhere this verdict does not address.

    The pass entry records the stamp and is posted as soon as it lands, so
    this refusal of the transition, like a transition the board loses,
    leaves the row and the last entry of its history naming the same
    commit. A criterion already finished at an earlier head and graded again
    at a later one is the case that matters: posted only once the moved
    sub-issue had been read back, the entry would be missing, the row would
    name the later commit while its history ended at the earlier one, and no
    later attempt's roster would reach the criterion to repair it.
    """
    finished_at, graded_at, state_name = MOVED_UNDER_THE_STAMP[moved]
    port = EditingBoard(
        target=CRITERIA[0],
        change={
            "state_kind": WorkflowStateKind.STARTED,
            "state_name": state_name,
        },
        after=0 if finished_at is None else 1,
    )
    lane_state = writer(port, lane_repo())
    if finished_at is not None:
        await tick(lane_state, sha=finished_at)
    before = stream(port)
    transitions, edits = list(port.workflow_writes), list(port.issue_writes)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha=graded_at)

    assert caught.value.target == CRITERIA[0]
    assert port.issues[CRITERIA[0]].state_kind is WorkflowStateKind.STARTED
    assert parse_criterion_evidence(port.issues[CRITERIA[0]].body) == CriterionEvidence(
        graded_sha=graded_at,
        test=evaluation_observation(session_id="eval-session", iteration=1),
    )
    assert port.workflow_writes == transitions
    assert [key for key, _, _ in port.issue_writes[len(edits) :]] == [CRITERIA[0]]
    posted = stream(port)
    passed = LaneRunEvent(
        kind=RunEventKind.CRITERION_PASSED,
        lane_key=LANE,
        subject_key=CRITERIA[0],
        graded_sha=graded_at,
    )
    assert posted == [*before, passed]
    assert [
        event
        for event in posted
        if event.subject_key == CRITERIA[0] and event.kind in EVIDENCE_ROW_WRITES
    ][-1] == passed
    history = evidence_row_history(events=posted, criterion_key=CRITERIA[0])
    assert restamp_verdict(history=history, graded_sha=graded_at) is (
        AuditVerdict.HOLDS
    )


@pytest.mark.parametrize(
    "second",
    [
        pytest.param("**Check:** a second Check row", id="check"),
        pytest.param("**Do:** a second build it names", id="do"),
        pytest.param("**Evidence:** an older row", id="evidence"),
        pytest.param("**Class:** one\n**Class:** another", id="class"),
    ],
)
async def test_a_tick_on_a_sub_issue_carrying_a_field_twice_writes_nothing(second):
    """The rows the stamp edits have to be one each, and that is knowable first.

    A body a person edited, or one rendered before the codec, can carry a
    template field twice. The field-scoped edit refuses such a body whichever
    row is doubled, so the fresh read asks the codec's own question the way
    it asks every other one this verdict depends on — before a byte is
    written, and not as an untyped failure after the grading session has
    already run.
    """
    ambiguous = CRITERIA[0]
    port = criteria_board(bodies={ambiguous: f"{criterion_body(ambiguous)}\n{second}"})
    lane_state = writer(port, lane_repo())
    before = board_shape(port)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="9" * 40)

    assert caught.value.target == ambiguous
    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []


def unsettable(key: str, *, hiding: str) -> str:
    """A criterion body no Evidence row can be set on, and why it reads so.

    The Check is the one the verdict was reached against and no template
    field is written twice, so every condition the rows alone answer is met.
    What the edit cannot do is set the Evidence row: *hiding* opens a fenced
    block or an HTML comment that nothing closes, so the row this write
    appends is inside it and reads back as no row at all.
    """
    return (
        f"**Check:** {check_of(key)}\n"
        f"**Do:** the build {key} names\n"
        f"{hiding}\n"
        "**Evidence:** —"
    )


#: The two openings a body can carry that swallow every line after them.
HIDING = [
    pytest.param("```", id="unclosed-fence-in-do"),
    pytest.param("<!--", id="unclosed-comment-in-do"),
]


@pytest.mark.parametrize("hiding", HIDING)
async def test_a_tick_on_a_body_no_evidence_row_can_be_set_on_writes_nothing(hiding):
    """The edit the tick makes is the whole precondition of making it.

    A body admitted at entry — one Check row, nothing written twice — can
    still be one the Evidence row cannot be set on, because the row the edit
    appends is hidden by an opening the body never closes. The write asks
    the edit itself before it touches the board, so such a body refuses as
    the same typed stale write as every other condition, rather than as an
    untyped failure raised after the grading session has already run.
    """
    ambiguous = CRITERIA[0]
    port = criteria_board(bodies={ambiguous: unsettable(ambiguous, hiding=hiding)})
    lane_state = writer(port, lane_repo())
    before = board_shape(port)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="e" * 40)

    assert caught.value.target == ambiguous
    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []


async def test_a_tick_on_a_sub_issue_with_no_evidence_row_writes_one():
    """A criterion nothing has recorded Evidence on is finished like any other.

    Entry requires a Check and nothing else, so a roster member can carry no
    Evidence row at all; the stamp appends the row it finds absent, and every
    byte the body already held stays where it was.
    """
    blank = CRITERIA[1]
    seed = f"**Check:** {check_of(blank)}\n**Do:** the build {blank} names"
    port = criteria_board(bodies={blank: seed})
    lane_state = writer(port, lane_repo())

    await tick(lane_state, sha="b" * 40)

    written = port.issues[blank]
    assert written.state_kind is WorkflowStateKind.COMPLETED
    assert parse_criterion_evidence(written.body) == CriterionEvidence(
        graded_sha="b" * 40,
        test=evaluation_observation(session_id="eval-session", iteration=1),
    )
    assert written.body.startswith(seed)
    assert without_evidence(written.body) == f"{without_evidence(seed)}\n"
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in CRITERIA
    )


def owning_closure(port: FakeTrackerPort) -> SubtreeClosure:
    """The rollup a reader of the owning issue answers its state from.

    Over the lane and the criteria under it: the plain child beside them
    carries no criterion of its own, which the rollup reads as a subtree
    nothing could finish rather than as this lane's gap.
    """
    return SubtreeClosure(
        facts={key: port.issues[key] for key in (LANE, *CRITERIA)},
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=LANE),
    )


def stream(port: FakeTrackerPort) -> list[LaneRunEvent]:
    """Every event this lane's stream holds, as the stream's own reader reads it."""
    return list(
        lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
    )


def subtree_closure(port: FakeTrackerPort) -> SubtreeClosure:
    """The rollup over the whole subtree the board holds beneath the lane.

    The facts are every row of the board, indexed the way production's own
    subtree read indexes them, so no descendant is left out by a list here.
    """
    ref = ScopeRef(kind=ScopeKind.ISSUE, key=LANE)
    return SubtreeClosure(
        facts=index_issue_tree(root=LANE, rows=tuple(port.issues.values()), ref=ref),
        ref=ref,
    )


def refutations(port: FakeTrackerPort) -> list[LaneRunEvent]:
    """The refutation events this lane's stream holds, in order."""
    return [
        event for event in stream(port) if event.kind is RunEventKind.CRITERION_REFUTED
    ]


async def test_a_passing_cross_off_records_its_grading_on_the_lanes_stream():
    """Every criterion an attempt finishes is one entry naming its own sha.

    The write that entry answers for is the Evidence row the passing cross-off
    restamped: the stream is read as that row's write history, so a restamp
    with no entry naming its commit reads as a row pointing behind the last
    grading that ran (KOD-506). The entry is addressed to the LANE, so no
    criterion sub-issue gains a comment of any kind, and the same verdict
    written again at the same head is the same entry and is recorded once.
    Each entry is followed by the lane's crossing-off of that criterion at
    the same sha, once the transition has landed.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())

    await tick(lane_state, sha="7" * 40)

    assert [
        (event.kind, event.subject_key, event.graded_sha) for event in stream(port)
    ] == [
        (kind, key, "7" * 40)
        for key in CRITERIA
        for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
    ]
    assert {comment.issue_key for comment in port.comments} == {LANE}

    at_first = len(port.comments)
    await tick(lane_state, sha="7" * 40)
    assert len(port.comments) == at_first

    # A later head restamps every row, and each restamp is its own entry:
    # collapsing them would leave the rows naming a commit the history does
    # not end at.
    await tick(lane_state, sha="8" * 40)
    assert [event.graded_sha for event in stream(port)] == [
        *["7" * 40] * (2 * len(CRITERIA)),
        *["8" * 40] * (2 * len(CRITERIA)),
    ]


def unverified(port: FakeTrackerPort) -> list[LaneRunEvent]:
    """The gradings this lane's stream says proved nothing, in order."""
    return [
        event
        for event in lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
        if event.kind is RunEventKind.CRITERION_GRADING_UNVERIFIED
    ]


def lapses(port: FakeTrackerPort) -> list[LaneRunEvent]:
    """The lapse announcements this lane's stream holds, in order."""
    return [
        event
        for event in lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
        if event.kind is RunEventKind.CRITERION_LAPSED
    ]


def crossings(port: FakeTrackerPort) -> list[tuple[str | None, str | None]]:
    """What this lane's stream says it crossed off, and at which sha, in order."""
    return [
        (event.subject_key, event.graded_sha)
        for event in lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
        if event.kind is RunEventKind.ISSUE_CROSSED_OFF
    ]


async def test_a_finished_criterion_is_announced_once_as_crossed_off():
    """The lane's own account of what it finished, once per grading.

    Each criterion the attempt passed is announced after its move to Done,
    keyed to it and carrying the sha it was graded at. The same verdict again
    at the same head repeats nothing; a re-grade at a later head is a second
    grading and is announced as one.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())

    await tick(lane_state, sha="1" * 40)
    first = [(key, "1" * 40) for key in CRITERIA]
    assert crossings(port) == first
    events = len(event_comments(port))

    await tick(lane_state, sha="1" * 40)
    assert crossings(port) == first
    assert len(event_comments(port)) == events

    await tick(lane_state, sha="2" * 40, keys=CRITERIA[:1])
    assert crossings(port) == [*first, (CRITERIA[0], "2" * 40)]


async def test_a_criterion_this_fire_finished_and_then_broke_is_taken_back():
    """A regression is recorded, not absorbed, and recorded exactly once.

    The first attempt finishes the criterion; the second fails it at a later
    head. The sub-issue goes back to the unstarted state with the refuting
    grading on its Evidence row, and the stream carries one refutation keyed
    to that criterion. A third attempt failing it again finds it unstarted —
    nothing this fire still claims — and writes nothing more.

    The issue that owns the criteria is written by nobody: it reaches its
    finished state by the tracker's own rollup once they are all finished,
    and the refutation reopens it by taking one of them back.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    assert owning_closure(port).is_closed(LANE)
    await tick(lane_state, sha="2" * 40, failed=[broken])

    issue = port.issues[broken]
    assert issue.state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(issue.body).graded_sha == "2" * 40
    assert [event.subject_key for event in refutations(port)] == [broken]
    assert [event.graded_sha for event in refutations(port)] == ["2" * 40]
    # The lane's own move-back is deliberately unleased: it holds no grant on
    # the criterion it takes back, and acquires none on the way (KOD-464).
    assert port.lease_writes == []
    assert port.lease_acquisitions == []
    # The issue that owns them reopened by the rollup, written by nobody.
    assert not owning_closure(port).is_closed(LANE)
    # The criteria the same attempt passed again are untouched by any of it.
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in CRITERIA[1:]
    )

    at_second = (board_shape(port), len(port.comments))
    await tick(lane_state, sha="2" * 40, failed=[broken])
    assert (board_shape(port), len(port.comments)) == at_second
    assert [event.subject_key for event in refutations(port)] == [broken]


@pytest.mark.parametrize(
    "drift,labels",
    [
        pytest.param(
            lambda body: body.replace(check_of(CRITERIA[0]), "an amended Check"),
            None,
            id="check-amended",
        ),
        pytest.param(None, frozenset(), id="classification-lost"),
        pytest.param(
            lambda body: f"{body}\n**Do:** a second build it names",
            None,
            id="row-duplicated",
        ),
    ],
)
async def test_a_regression_on_a_sub_issue_that_drifted_takes_nothing_back(
    drift, labels
):
    """The move back asserts about the sub-issue what the tick asserted.

    A criterion whose Check was amended, whose classification is gone, or
    whose body gained a second row of a template field is no longer the one
    this verdict was reached against, and taking it back would write the
    refuting grading onto a sub-issue the verdict does not address. The
    refusal is the same typed one the tick makes, before any write.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    drifted = CRITERIA[0]
    await tick(lane_state, sha="1" * 40)
    update: dict[str, object] = {}
    if drift is not None:
        update["body"] = drift(port.issues[drifted].body)
    if labels is not None:
        update["issue_labels"] = labels
    port.issues[drifted] = port.issues[drifted].model_copy(update=update)
    finished = board_shape(port)
    writes = (list(port.issue_writes), list(port.workflow_writes))

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="2" * 40, failed=[drifted])

    assert caught.value.target == drifted
    assert board_shape(port) == finished
    assert (port.issue_writes, port.workflow_writes) == writes
    assert refutations(port) == []


class MovingBoard(FakeTrackerPort):
    """A board that rewrites the addressed sub-issue inside the move back.

    The edit lands after the write the act starts with and before the
    Evidence row it goes on to set, which is the one window in which the body
    the act's precondition was read from and the body that row is set against
    are different: the port's own move back writes no body, so nothing else
    models a third party editing one there.
    """

    def __init__(self, *, target: str, edit) -> None:
        source = criteria_board()
        super().__init__(
            issues=list(source.issues.values()),
            marker_prefixes=lane_operation().marker_prefixes,
        )
        self._target, self._edit = target, edit

    async def reset_criterion_pending(self, *, expected, holder=None):
        moved = await super().reset_criterion_pending(expected=expected, holder=holder)
        if expected.issue_key == self._target:
            stored = self.issues[self._target]
            self.issues[self._target] = stored.model_copy(
                update={"body": self._edit(stored.body)}
            )
        return moved


async def test_a_check_amended_under_the_move_back_leaves_the_criterion_owed():
    """The row is set against the body the move back left, and asserts about it.

    A third party amends the Check between the move and the stamp, so the
    sub-issue that row would land on is no longer the one this verdict
    addresses and the act stops there. What it leaves is the partial state a
    lost stamp leaves: unstarted and owed, carrying the grading that finished
    it, with nothing on the stream saying it was taken back.
    """
    broken = CRITERIA[0]
    port = MovingBoard(
        target=broken,
        edit=lambda body: body.replace(check_of(broken), "an amended Check"),
    )
    lane_state = writer(port, lane_repo())
    await tick(lane_state, sha="1" * 40)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="2" * 40, failed=[broken])

    assert caught.value.target == broken
    assert port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(port.issues[broken].body).graded_sha == "1" * 40
    assert refutations(port) == []


async def test_a_do_row_edited_under_the_move_back_is_kept_under_the_stamp():
    """An edit the verdict does not address survives the row the act sets.

    The Evidence row is set by compare-and-set against the sub-issue as the
    move back left it rather than against the body the act was decided on, so
    a row this verdict says nothing about is kept and the refutation
    completes: the criterion unstarted, the refuting grading recorded, one
    event on the stream.
    """
    broken = CRITERIA[0]
    rewritten = "the build somebody else described"
    port = MovingBoard(
        target=broken,
        edit=lambda body: replace_criterion_fields(
            body, replacements={"Do": rewritten}
        ),
    )
    lane_state = writer(port, lane_repo())
    await tick(lane_state, sha="1" * 40)

    await tick(lane_state, sha="2" * 40, failed=[broken])

    issue = port.issues[broken]
    assert issue.state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(issue.body).graded_sha == "2" * 40
    assert criterion_field_bodies(issue.body, field="Do") == (rewritten,)
    assert [event.graded_sha for event in refutations(port)] == ["2" * 40]


@pytest.mark.parametrize("hiding", HIDING)
async def test_a_regression_on_a_body_no_evidence_row_can_be_set_on_is_refused(hiding):
    """The act's last write is a precondition of its first one.

    A body edited into a shape the Evidence row cannot be set on is refused
    before the move back rather than after it: the criterion is still the
    pass it was, carrying the grading that finished it, and the board holds
    nothing that says otherwise. Asked after the move back, the same
    refusal would leave the criterion unstarted for a fault that was
    knowable while it was still finished.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    drifted = CRITERIA[0]
    await tick(lane_state, sha="1" * 40)
    port.issues[drifted] = port.issues[drifted].model_copy(
        update={"body": unsettable(drifted, hiding=hiding)}
    )
    finished = board_shape(port)
    writes = (list(port.issue_writes), list(port.workflow_writes))

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="2" * 40, failed=[drifted])

    assert caught.value.target == drifted
    assert port.issues[drifted].state_kind is WorkflowStateKind.COMPLETED
    assert board_shape(port) == finished
    assert (port.issue_writes, port.workflow_writes) == writes
    assert refutations(port) == []


async def test_one_grading_broken_twice_is_still_one_refutation():
    """The same refutation with no row write between is posted once.

    The criterion is broken at a head, moved back into the finished state
    by hand, and broken at that same head once more: two verdicts about one
    grading. The second break enters the act again — the criterion is
    finished, so there is something to take back — and the criterion's last
    row-write entry is already the refutation that names this lane, this
    criterion and this sha, so nothing is added to the stream. The sub-issue
    is taken back either way.

    The move back is by hand because it writes no Evidence row. A pass at
    that head between the two breaks would restamp the row and be the
    history's last write, so the second break would be a row write of its
    own and announced again (KOD-506). The every-state case below revisits
    a commit across two heads only; the same-head pass between two breaks
    is pinned by the same-head interleave case of
    ``test_a_pass_and_a_refutation_at_one_head_are_each_a_row_write``.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    await tick(lane_state, sha="2" * 40, failed=[broken])
    await port.set_workflow_state(issue_key=broken, stage=LifecycleStage.DONE)
    assert port.issues[broken].state_kind is WorkflowStateKind.COMPLETED

    await tick(lane_state, sha="2" * 40, failed=[broken])

    assert port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert [event.graded_sha for event in refutations(port)] == ["2" * 40]


PASSED, REFUTED = RunEventKind.CRITERION_PASSED, RunEventKind.CRITERION_REFUTED
CROSSED = RunEventKind.ISSUE_CROSSED_OFF


@pytest.mark.parametrize(
    "steps,expected",
    [
        pytest.param(
            [("1", True), ("1", False)],
            [(PASSED, "1"), (CROSSED, "1"), (REFUTED, "1")],
            id="pass-then-break-at-one-head",
        ),
        pytest.param(
            [("1", True), ("2", False), ("2", True)],
            [
                (PASSED, "1"),
                (CROSSED, "1"),
                (REFUTED, "2"),
                (PASSED, "2"),
                (CROSSED, "2"),
            ],
            id="break-then-pass-at-one-head",
        ),
        pytest.param(
            [("1", True), ("2", True), ("2", False), ("2", True), ("2", False)],
            [
                (PASSED, "1"),
                (CROSSED, "1"),
                (PASSED, "2"),
                (CROSSED, "2"),
                (REFUTED, "2"),
                (PASSED, "2"),
                (REFUTED, "2"),
            ],
            id="same-head-interleave",
        ),
    ],
)
async def test_a_pass_and_a_refutation_at_one_head_are_each_a_row_write(
    steps, expected
):
    """The rule compares the kind of the last row write, not only its sha.

    A pass and a refutation at the same head are two different writes of
    the Evidence row: each restamps it with its own verdict, so each is an
    entry of its own though both name one commit. The de-duplication rule
    asks whether the criterion's last row write is already this entry, kind
    and sha together (KOD-506); a rule that compared the sha alone would
    read the second verdict at a head as a repeat of the first and leave
    the history ending at a write the row no longer carries. After every
    tick, the last commit the history names is the one the row names, and
    the entries on the stream are exactly the row writes in order, each pass
    followed by the lane's crossing-off unless the stream already holds that
    same crossing-off.
    """
    subject = CRITERIA[0]
    port = criteria_board(keys=[subject])
    lane_state = writer(port, lane_repo())

    for digit, passing in steps:
        await tick(
            lane_state,
            sha=digit * 40,
            keys=[subject],
            failed=[] if passing else [subject],
        )
        row = parse_criterion_evidence(port.issues[subject].body).graded_sha
        history = evidence_row_history(events=stream(port), criterion_key=subject)
        assert history[-1] == row

    assert [
        (event.kind, event.graded_sha)
        for event in stream(port)
        if event.subject_key == subject
    ] == [(kind, digit * 40) for kind, digit in expected]


async def test_a_criterion_this_fire_never_finished_is_not_taken_back():
    """A criterion that never passed is still owed, and no event says otherwise."""
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    before = board_shape(port)

    await tick(lane_state, sha="3" * 40, failed=CRITERIA)

    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []
    assert refutations(port) == []


async def test_a_finished_criterion_outside_the_dispatched_roster_is_left_alone():
    """Another run's finished work is not this verdict's to take back."""
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    outside = CRITERIA[2]
    port.issues[outside] = port.issues[outside].model_copy(
        update={"state_kind": WorkflowStateKind.COMPLETED, "state_name": "done"}
    )
    before = board_shape(port)[outside]

    await tick(lane_state, sha="5" * 40, keys=CRITERIA[:2], failed=CRITERIA[:2])

    assert board_shape(port)[outside] == before
    assert refutations(port) == []


async def test_an_undemonstrated_attempt_takes_nothing_back():
    """A grading that proved nothing takes back no more than it crosses off."""
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    await tick(lane_state, sha="6" * 40)
    finished = board_shape(port)

    await tick(lane_state, sha="7" * 40, reasons=withheld())

    assert board_shape(port) == finished
    assert refutations(port) == []
    assert [event.subject_key for event in unverified(port)] == list(CRITERIA)


async def test_an_undemonstrated_criterion_is_recorded_on_the_stream_and_nowhere_else():
    """The reading that failed reaches the lane's stream and no sub-issue.

    One event per criterion, keyed to its own sub-issue and carrying the sha
    the verdict would have been stamped with, on a board where nothing else
    moved: no state, no Evidence row, and no refutation, because a grading
    that read nothing about a criterion is no reading that it broke.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    before = board_shape(port)

    await tick(lane_state, sha="8" * 40, reasons=withheld())

    assert board_shape(port) == before
    assert port.workflow_writes == []
    assert port.issue_writes == []
    assert refutations(port) == []
    assert [
        (event.subject_key, event.graded_sha, event.lane_key)
        for event in unverified(port)
    ] == [(key, "8" * 40, LANE) for key in CRITERIA]


@pytest.mark.parametrize("reason", list(UndemonstratedReason))
async def test_every_reading_that_came_back_empty_is_recorded_under_its_own_kind(
    reason,
):
    """Whichever reading failed, the stream names that one and the board is still.

    The base readings go through the same act as the workspace reading: one
    event of the kind the reason joins to, keyed to the criterion's sub-issue
    at the sha the verdict would have been stamped with, and no state, no
    Evidence row and no event of any other reading's kind.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    before = board_shape(port)
    unread = CRITERIA[0]

    await tick(
        lane_state, sha="8" * 40, keys=[unread], reasons=withheld([unread], reason)
    )

    posted = lane_run_events(
        comments=port.comments,
        lane_key=LANE,
        marker_prefixes=lane_operation().marker_prefixes,
    )
    assert board_shape(port) == before
    assert port.workflow_writes == []
    assert port.issue_writes == []
    assert [(event.kind, event.subject_key, event.graded_sha) for event in posted] == [
        (UNDEMONSTRATED_EVENT_KINDS[reason], unread, "8" * 40)
    ]


@pytest.mark.parametrize("reason", list(UndemonstratedReason))
async def test_the_rows_write_history_ends_where_the_row_does_in_every_state(reason):
    """No state a cross-off leaves has a row behind its own write history.

    Four criteria are finished at one head, and the next attempt leaves each
    in a different state: two refuted and taken back, one read as
    undemonstrated under *reason*, one passed again; that one then lapses at
    a third head. The head then returns to the first commit, where one of
    the refuted two passes again, and moves on to the second, where it is
    refuted again. The audit's restamp trace reads the lane's stream as each
    Evidence row's write history (KOD-506), so for every criterion the last
    commit that history names must be the one its row names.

    The return is a head revisiting a commit, as a divergence recovery that
    resets the workspace to the remote tip can. The pass there restamps the
    row at the first commit after the refutation at the second, and the
    refutation after it restamps the row at the second again after that
    pass, so each is a row write of its own and is announced again, though
    an equal entry sits earlier in the stream: looked up anywhere in the
    stream rather than as the criterion's last row write, either would leave
    the history ending at the other while the row names its own commit. The
    other refuted criterion stays where the refutation left it, so the
    refuted state still ends in a criterion of its own.

    The lapse announces itself under its own kind, which records no row
    write: the one that lapses keeps exactly the passes it was finished with
    as its row's history, and its lapse entry is none of it. Each pass is
    followed by the lane's crossing-off, unless the stream already holds that
    same crossing-off, as it does for the return to the first commit.

    The undemonstrated one is the case that can go wrong. It is still
    finished, its row still names the first head, and its reading posted its
    own event at the second: that event is its only entry at the second head,
    and no ``criterion_passed`` is posted for it, because nothing restamped
    its row (KOD-610). A writer that posted a pass for it, or a history that
    read its reading as a row write, leaves the row behind the history.
    """
    broken, revisited, unread, kept = keys = (*CRITERIA, f"{LANE}/fourth")
    port = criteria_board(keys=keys)
    lane_state = writer(port, lane_repo())
    first, second, third = "1" * 40, "2" * 40, "3" * 40
    await tick(lane_state, sha=first, keys=keys)

    await tick(
        lane_state,
        sha=second,
        keys=keys,
        failed=[broken, revisited],
        reasons=withheld([unread], reason),
    )
    await lapse(lane_state, key=kept, standing_sha=second, head_sha=third)
    await tick(lane_state, sha=first, keys=[revisited])
    await tick(lane_state, sha=second, keys=[revisited], failed=[revisited])

    posted = stream(port)

    def entries(key: str) -> list[tuple[RunEventKind, str | None]]:
        return [
            (event.kind, event.graded_sha)
            for event in posted
            if event.subject_key == key
        ]

    assert entries(unread) == [
        (RunEventKind.CRITERION_PASSED, first),
        (RunEventKind.ISSUE_CROSSED_OFF, first),
        (UNDEMONSTRATED_EVENT_KINDS[reason], second),
    ]
    assert entries(broken) == [
        (RunEventKind.CRITERION_PASSED, first),
        (RunEventKind.ISSUE_CROSSED_OFF, first),
        (RunEventKind.CRITERION_REFUTED, second),
    ]
    assert entries(revisited) == [
        (RunEventKind.CRITERION_PASSED, first),
        (RunEventKind.ISSUE_CROSSED_OFF, first),
        (RunEventKind.CRITERION_REFUTED, second),
        (RunEventKind.CRITERION_PASSED, first),
        (RunEventKind.CRITERION_REFUTED, second),
    ]
    assert entries(kept) == [
        (RunEventKind.CRITERION_PASSED, first),
        (RunEventKind.ISSUE_CROSSED_OFF, first),
        (RunEventKind.CRITERION_PASSED, second),
        (RunEventKind.ISSUE_CROSSED_OFF, second),
        (RunEventKind.CRITERION_LAPSED, second),
    ]
    assert port.issues[unread].state_kind is WorkflowStateKind.COMPLETED
    assert port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert port.issues[revisited].state_kind is WorkflowStateKind.UNSTARTED
    rows = {
        key: parse_criterion_evidence(port.issues[key].body).graded_sha for key in keys
    }
    assert rows == {broken: second, revisited: second, unread: first, kept: second}
    for key in keys:
        history = evidence_row_history(events=posted, criterion_key=key)
        assert history[-1] == rows[key], key
        assert restamp_verdict(history=history, graded_sha=rows[key]) is (
            AuditVerdict.HOLDS
        )


async def test_a_refutation_and_an_unverified_reading_share_one_board_read():
    """One board reading serves whatever the act writes.

    A roster holding a regression and a criterion the attempt read nothing
    about writes two kinds of event; both are de-duplicated against the same
    snapshot, taken once, before the first sub-issue is read.
    """
    broken, unread = CRITERIA[0], CRITERIA[1]
    port = counting_criteria_board()
    lane_state = writer(port, lane_repo())
    await tick(lane_state, sha="1" * 40)

    port.count_the_next_write()
    await tick(
        lane_state,
        sha="2" * 40,
        failed=[broken],
        reasons=withheld([unread]),
    )

    assert port.listings == 1
    assert [event.subject_key for event in refutations(port)] == [broken]
    assert [event.subject_key for event in unverified(port)] == [unread]


async def test_an_attempt_that_passed_everything_reads_the_board_once():
    """Every pass looks its entry up in the act's one reading of the stream.

    The stream is read for the events this act might post. A passing
    cross-off now records its grading there (KOD-506), so an attempt that
    passed everything has an entry per criterion to de-duplicate, and it
    pays for that once for the whole act rather than once per criterion.
    The same verdict at the same head then posts nothing: each entry is
    found in that one reading.

    A lapse now announces itself too, so the lapse case below reads the
    board once as well, and asserts it.
    """
    port = counting_criteria_board()
    lane_state = writer(port, lane_repo())

    await tick(lane_state, sha="3" * 40)

    assert port.listings == 1
    assert [(event.kind, event.subject_key) for event in stream(port)] == [
        (kind, key)
        for key in CRITERIA
        for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
    ]

    port.count_the_next_write()
    posted = len(port.comments)
    await tick(lane_state, sha="3" * 40)

    assert port.listings == 1
    assert len(port.comments) == posted


async def test_a_second_undemonstrated_grading_at_a_later_head_is_its_own_event():
    """The event names the grading, so two gradings are two events.

    Repeated at the SAME head the reading is the same value and adds
    nothing, which is what makes a graph-level retry of one iteration free.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    unread = CRITERIA[0]

    await tick(lane_state, sha="1" * 40, reasons=withheld([unread]))
    await tick(lane_state, sha="1" * 40, reasons=withheld([unread]))
    await tick(lane_state, sha="2" * 40, reasons=withheld([unread]))

    assert [event.graded_sha for event in unverified(port)] == ["1" * 40, "2" * 40]
    assert {event.subject_key for event in unverified(port)} == {unread}


async def test_a_second_regression_at_a_later_head_is_its_own_refutation():
    """Two gradings are two events, because the event names the grading.

    The criterion passes, breaks, is finished again and breaks again at a
    third head. Each break is a regression of its own and is recorded as
    one; a refutation keyed to the criterion alone would read the second as
    a repeat of the first and absorb it. A failing verdict repeated at the
    SAME head finds the criterion unstarted and adds nothing.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    await tick(lane_state, sha="2" * 40, failed=[broken])
    await tick(lane_state, sha="3" * 40)
    await tick(lane_state, sha="4" * 40, failed=[broken])

    assert [event.graded_sha for event in refutations(port)] == ["2" * 40, "4" * 40]
    assert {event.subject_key for event in refutations(port)} == {broken}
    assert port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(port.issues[broken].body).graded_sha == "4" * 40

    at_fourth = (board_shape(port), len(port.comments))
    await tick(lane_state, sha="4" * 40, failed=[broken])
    assert (board_shape(port), len(port.comments)) == at_fourth


def losing_board() -> LosingBoard:
    """The tick fixture's own board, ready to lose one named write."""
    source = criteria_board()
    return LosingBoard(
        issues=list(source.issues.values()),
        marker_prefixes=lane_operation().marker_prefixes,
    )


#: What each lost write of the refutation leaves on the sub-issue: the state
#: it is in, the grading its Evidence row carries, and whether a later
#: failing verdict at the same head still completes the act.
LOST_WRITES = {
    "reset_criterion_pending": (WorkflowStateKind.COMPLETED, "1" * 40, True),
    "edit_description": (WorkflowStateKind.UNSTARTED, "1" * 40, False),
    "post_run_event": (WorkflowStateKind.UNSTARTED, "2" * 40, False),
}


@pytest.mark.parametrize("drops", sorted(LOST_WRITES))
async def test_a_refutation_a_write_was_lost_from_leaves_the_criterion_owed(drops):
    """Whatever the act loses, the board never certifies the grading that failed.

    The move back is the first write, so a failure after it leaves the
    criterion unstarted — owed, carrying the earlier grading and no event,
    which the next fire's roster reads as work to do again. A failure AT it
    leaves the criterion exactly the pass it was, and the next failing
    verdict at the same head performs the whole act. What no loss leaves is
    a criterion finished at a sha that failed it, and nothing repairs the
    event for a criterion already taken back: unstarted, it is no longer
    this fire's claim.
    """
    state, recorded, completed_later = LOST_WRITES[drops]
    port = losing_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    finished = board_shape(port)
    port.lose(drops)
    with pytest.raises(TransientAPIError):
        await tick(lane_state, sha="2" * 40, failed=[broken])

    assert port.issues[broken].state_kind is state
    assert parse_criterion_evidence(port.issues[broken].body).graded_sha == recorded
    assert refutations(port) == []
    assert {
        key: shape for key, shape in board_shape(port).items() if key != broken
    } == {key: shape for key, shape in finished.items() if key != broken}

    await tick(lane_state, sha="2" * 40, failed=[broken])

    assert port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(port.issues[broken].body).graded_sha == (
        "2" * 40 if completed_later else recorded
    )
    assert [event.graded_sha for event in refutations(port)] == (
        ["2" * 40] if completed_later else []
    )


#: What each lost write of a pass leaves on a criterion finished at the first
#: head and graded again at the second: the grading its Evidence row carries,
#: and the pass entries that second grading posted.
LOST_PASS_WRITES = {
    "edit_description": ("1" * 40, ()),
    "set_workflow_state": ("2" * 40, ("2" * 40,)),
}


@pytest.mark.parametrize("drops", sorted(LOST_PASS_WRITES))
async def test_a_pass_a_write_was_lost_from_leaves_the_row_where_its_history_ends(
    drops,
):
    """The pass entry follows the row write it records, and nothing else.

    A criterion finished at one head is graded again at a later one, and one
    write of that pass is lost. A stamp that never landed posts nothing, so
    the row and its history both stay at the earlier grading. A transition
    lost after the stamp leaves the criterion finished with its row at the
    later commit, and the entry naming that commit is already on the stream:
    posted after the transition, it would be missing, and nothing would
    repair it, because a finished criterion is in no later attempt's roster.
    Either way the restamp trace holds for the row the board keeps.
    """
    recorded, later = LOST_PASS_WRITES[drops]
    port = losing_board()
    lane_state = writer(port, lane_repo())
    regraded, *rest = CRITERIA

    await tick(lane_state, sha="1" * 40)
    port.lose(drops)
    with pytest.raises(TransientAPIError):
        await tick(lane_state, sha="2" * 40)

    assert port.issues[regraded].state_kind is WorkflowStateKind.COMPLETED
    row = parse_criterion_evidence(port.issues[regraded].body).graded_sha
    assert row == recorded
    posted = stream(port)
    assert [(event.kind, event.subject_key, event.graded_sha) for event in posted] == [
        *(
            (kind, key, "1" * 40)
            for key in CRITERIA
            for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
        ),
        *((RunEventKind.CRITERION_PASSED, regraded, sha) for sha in later),
    ]
    assert all(
        parse_criterion_evidence(port.issues[key].body).graded_sha == "1" * 40
        for key in rest
    )
    history = evidence_row_history(events=posted, criterion_key=regraded)
    assert history == ("1" * 40, *later)
    assert restamp_verdict(history=history, graded_sha=row) is AuditVerdict.HOLDS


async def test_a_stream_that_will_not_parse_refuses_before_the_board_is_touched():
    """A damaged stream is knowable first, so the pass still reads as a pass.

    The board and its stream are read before anything is written. Placed
    after the stamp, the refusal would leave the sub-issue finished with the
    FAILING grading's sha on its Evidence row, which reads as a pass at a
    commit nothing passed at.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]
    await tick(lane_state, sha="1" * 40)
    prefix = lane_operation().marker_prefixes["run_event"]
    await port.post_comment(
        issue_key=LANE, body=f"[{prefix}:{LANE}]\n```json\n{{not json at all"
    )
    finished_board = board_shape(port)

    with pytest.raises(LaneRecordWriteError, match="event stream"):
        await tick(lane_state, sha="2" * 40, failed=[broken])

    assert board_shape(port) == finished_board
    assert port.issues[broken].state_kind is WorkflowStateKind.COMPLETED
    assert parse_criterion_evidence(port.issues[broken].body).graded_sha == "1" * 40


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(
            {"state_kind": WorkflowStateKind.STARTED, "state_name": "In Progress"},
            id="started",
        ),
        pytest.param(
            {"state_kind": WorkflowStateKind.CANCELED, "state_name": "Canceled"},
            id="cancelled",
        ),
    ],
)
async def test_a_failed_verdict_on_a_criterion_in_another_state_writes_nothing(state):
    """A fail writes nothing unless it is a regression, and that means finished.

    The board moved the criterion somewhere this verdict does not address.
    A fail is not a claim about it, so there is nothing to take back and
    nothing to refuse: the unwritten verdict is on the iteration event and
    in the writer's own line.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    moved = CRITERIA[0]
    port.issues[moved] = port.issues[moved].model_copy(update=state)
    before = board_shape(port)

    await tick(lane_state, sha="8" * 40, keys=[moved], failed=[moved])

    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []
    assert refutations(port) == []


# ---------------------------------------------------------------------------
# The lapse: the same take-back, with nothing to report.
# ---------------------------------------------------------------------------


async def lapse(
    lane_state,
    *,
    key: str,
    standing_sha: str,
    head_sha: str,
) -> None:
    """One attempt whose reading of *key*'s standing grading is that it lapsed.

    The standing cross-off is the one an earlier attempt finished the
    criterion with, so the reading arrives the way the loop hands it over:
    the grading it was taken at, and the verdict arithmetic reached about it.
    """
    standing = cross_offs_for(
        results=graded([key]),
        graded_sha=standing_sha,
        observation=evaluation_observation(session_id="eval-session", iteration=1),
        reasons={},
    )
    await lane_state.write_cross_offs(
        lane=binding(),
        dispatched=dispatched([key]),
        cross_offs=cross_offs_for(
            results=graded([key]),
            graded_sha=head_sha,
            observation=evaluation_observation(session_id="eval-session", iteration=2),
            reasons={},
            standing=standing,
            reading={criterion_ref(CriterionId(key)): GradedState.lapsed},
        ),
    )


async def test_a_lapsed_grading_is_taken_back_with_the_sha_it_was_graded_at():
    """The board says the criterion is owed again, and the gap stays legible.

    The sub-issue leaves the finished state for the team's unstarted one,
    and its Evidence row keeps the sha the lapsed grading was taken at with
    a pointer saying that grading lapsed — a reader sees the gap between
    what was graded and where the branch went, rather than a satisfied
    criterion. The issue that owns it reopens by the tracker's own rollup,
    written by nobody, while every criterion beside it stays finished.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    lapsing = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    assert owning_closure(port).is_closed(LANE)

    await lapse(lane_state, key=lapsing, standing_sha="1" * 40, head_sha="2" * 40)

    issue = port.issues[lapsing]
    assert issue.state_kind is WorkflowStateKind.UNSTARTED
    evidence = parse_criterion_evidence(issue.body)
    assert evidence.graded_sha == "1" * 40
    assert evidence.test == lapse_observation(
        observation=evaluation_observation(session_id="eval-session", iteration=1)
    )
    assert not owning_closure(port).is_closed(LANE)
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in CRITERIA[1:]
    )


async def test_a_lapse_is_announced_as_a_lapse_and_never_as_a_refutation():
    """A lapse is not a regression, and the stream says which one happened.

    Both take-backs read the stream first, to tell a second announcement of
    one grading from the first, and both then post under their own kind: the
    lapse as ``criterion_lapsed``, the refutation as ``criterion_refuted``. A
    lapse announced as nothing at all would leave a reader of the stream a
    finished criterion moved back that nobody reported, which is what a
    regression looks like.
    """
    operation = lane_operation()
    port = CountingBoard(
        issues=[
            make_tracker_issue(LANE, body="the lane's own text"),
            *(
                make_tracker_issue(
                    key,
                    parent_key=LANE,
                    issue_labels=frozenset({"criterion"}),
                    body=criterion_body(key),
                )
                for key in CRITERIA
            ),
        ],
        marker_prefixes=operation.marker_prefixes,
    )
    lane_state = writer(port, lane_repo())
    lapsing, broken = CRITERIA[0], CRITERIA[1]
    await tick(lane_state, sha="1" * 40)

    port.count_the_next_write()
    finished = stream(port)
    await lapse(lane_state, key=lapsing, standing_sha="1" * 40, head_sha="2" * 40)
    assert port.listings == 1
    assert stream(port) == [
        *finished,
        LaneRunEvent(
            kind=RunEventKind.CRITERION_LAPSED,
            lane_key=LANE,
            subject_key=lapsing,
            graded_sha="1" * 40,
        ),
    ]
    assert refutations(port) == []
    assert [(event.subject_key, event.graded_sha) for event in lapses(port)] == [
        (lapsing, "1" * 40)
    ]

    port.count_the_next_write()
    await tick(lane_state, sha="2" * 40, keys=[broken], failed=[broken])
    assert port.listings == 1
    assert [event.subject_key for event in refutations(port)] == [broken]


# ---------------------------------------------------------------------------
# A node's observed session openings, on the lane's own stream.
# ---------------------------------------------------------------------------

OPENED_BY = RunIdentity(
    kind=RunKind.FIRE, name=LANE, started_at=datetime(2026, 1, 1, tzinfo=UTC)
)


def opening(session_id: str, *, invocation_key: str = "evaluation-1"):
    return NodeSessionStartedEvent(
        invocation=NodeInvocation(
            run=OPENED_BY,
            node_key="evaluation",
            invocation_key=invocation_key,
            declared_sessions=1,
        ),
        session_id=session_id,
    )


def openings_on(port: FakeTrackerPort) -> list[NodeSessionKey]:
    """The session openings this lane's stream holds, read back as their keys."""
    return [
        NodeSessionKey.model_validate_json(event.subject_key or "")
        for event in lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
        if event.kind is RunEventKind.NODE_SESSION_STARTED
    ]


async def test_node_sessions_are_posted_once_each_under_their_invocation():
    """Each opening once, keyed to its whole invocation and its session.

    Two openings of one invocation are two events; the same two handed over
    again — a resumed lane, or a retried post — add nothing; a third opening
    later is posted on its own.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    first, second = opening("session-a"), opening("session-b")

    await lane_state.record_node_sessions(lane=binding(), started=(first, second))
    await lane_state.record_node_sessions(lane=binding(), started=(first, second))
    third = opening("session-c", invocation_key="evaluation-2")
    await lane_state.record_node_sessions(lane=binding(), started=(third,))

    assert openings_on(port) == [
        NodeSessionKey(invocation=event.invocation, session_id=event.session_id)
        for event in (first, second, third)
    ]
    assert len(event_comments(port)) == 3


async def test_an_evaluation_that_observed_no_opening_reads_and_writes_nothing():
    port = CountingBoard(
        issues=[make_tracker_issue(LANE, body="the lane's own text")],
        marker_prefixes=lane_operation().marker_prefixes,
    )
    lane_state = writer(port, lane_repo())
    port.count_the_next_write()

    await lane_state.record_node_sessions(lane=binding(), started=())

    assert port.listings == 0
    assert port.comments == []


# The rollup: one lane check, four answers, over the board the writer left.
# ---------------------------------------------------------------------------

#: The lane check of this fire: a criterion sub-issue like its siblings.
LANE_CHECK = CRITERIA[2]
STANDING_SHA = "1" * 40
LATER_SHA = "2" * 40


async def rollup_board(arm: str) -> tuple[FakeTrackerPort, SubtreeClosure]:
    """The board the real writer leaves after one of the five arms.

    Every sub-issue beneath the fire is a criterion or owns one, so the
    rollup reads the whole subtree production reads.  The child's criterion
    is finished by the same writer, bound to that child, on every arm but
    the two that leave a descendant criterion open.
    """
    port = criteria_board(depth=2 if arm == "deep" else 1)
    lane_state = writer(port, lane_repo())
    await tick(lane_state, sha=STANDING_SHA)
    if arm != "descendant":
        await tick(
            lane_state,
            sha=STANDING_SHA,
            keys=[CHILD_CRITERION],
            lane=dataclasses.replace(binding(), lane_key=CHILD),
        )
    match arm:
        case "refuted":
            await tick(lane_state, sha=LATER_SHA, failed=[LANE_CHECK])
        case "lapsed":
            await lapse(
                lane_state,
                key=LANE_CHECK,
                standing_sha=STANDING_SHA,
                head_sha=LATER_SHA,
            )
        case "met" | "descendant" | "deep":
            pass
    return port, subtree_closure(port)


@pytest.mark.parametrize(
    ("arm", "gap", "graded_at", "refuted", "check_state"),
    [
        pytest.param(
            "refuted",
            (LANE_CHECK,),
            LATER_SHA,
            [LANE_CHECK],
            WorkflowStateKind.UNSTARTED,
            id="refuted",
        ),
        pytest.param(
            "met", (), STANDING_SHA, [], WorkflowStateKind.COMPLETED, id="met"
        ),
        pytest.param(
            "lapsed",
            (LANE_CHECK,),
            STANDING_SHA,
            [],
            WorkflowStateKind.UNSTARTED,
            id="lapsed",
        ),
        pytest.param(
            "descendant",
            (CHILD_CRITERION,),
            STANDING_SHA,
            [],
            WorkflowStateKind.COMPLETED,
            id="descendant",
        ),
        pytest.param(
            "deep",
            (GRANDCHILD_CRITERION,),
            STANDING_SHA,
            [],
            WorkflowStateKind.COMPLETED,
            id="deep",
        ),
    ],
)
async def test_the_rollup_over_the_subtree_answers_one_lane_check_four_ways(
    arm, gap, graded_at, refuted, check_state
):
    """A fire's Done is the rollup over every criterion sub-issue beneath it.

    The lane check is ``LANE/third``.  That a fire's lane checks are criterion
    sub-issues of the fire is shown by structure, not by wording: it carries
    the same label, is written by the same ``write_cross_offs``, makes the
    same state move, keeps the same Evidence row and is read by the same gap
    arm as its siblings.  That no second set of lane checks is written by
    another path is held by ``tests/test_issue_state_write_sites.py``.  Every
    finished criterion was finished by the writer, and the rollup reads every
    row of the board through production's own subtree indexing; the open
    descendant criterion on the last two rows is open by the board's default.

    The gap attaches no verdict: each member is the board row itself.  On the
    descendant row the fire's own family is all finished, so a reading that
    stopped at the direct family would call the fire done (KOD-790); on the
    deep row the open criterion sits two containers down, so the rollup is
    recursive and not one level deep.

    The fire's state is asked first, of a closure nothing has read yet, so
    it is computed and not read back from the gap below.  The lane check's
    own state is read too: a failing grade and a lapse each leave it in the
    unstarted state, a met grading leaves it completed.
    """
    port, closure = await rollup_board(arm)

    assert closure.is_closed(LANE) is (gap == ())
    found = closure.gap(LANE)
    assert tuple(row.issue_key for row in found) == gap
    assert all(row is port.issues[row.issue_key] for row in found)
    assert port.issues[LANE_CHECK].state_kind is check_state
    assert parse_criterion_evidence(port.issues[LANE_CHECK].body).graded_sha == (
        graded_at
    )
    assert [event.subject_key for event in refutations(port)] == refuted
    if arm in {"descendant", "deep"}:
        assert open_criteria(closure.criteria(LANE), ref=closure.ref) == ()


async def test_a_lapsed_and_a_refuted_lane_check_leave_one_board_and_two_streams():
    """Ungraded rather than failed is a fact the writer leaves, not one authored.

    Both arms leave the lane check in the same state, with a body identical
    outside its Evidence row and an Evidence row of the same shape, and both
    reopen the fire by the rollup; only the stream tells them apart, with one
    refutation for the failed grading and none for the lapsed one.  The two
    Evidence rows are not byte-equal: each carries the sha its own arm
    graded at, which the four-way rollup test asserts row by row.  The
    writer's own side of the distinction is pinned by
    ``test_a_lapse_announces_nothing_and_asks_the_board_nothing_extra``.
    """
    refuted_port, refuted_closure = await rollup_board("refuted")
    lapsed_port, lapsed_closure = await rollup_board("lapsed")
    refuted_row = refuted_port.issues[LANE_CHECK]
    lapsed_row = lapsed_port.issues[LANE_CHECK]

    assert refuted_row.state_kind is lapsed_row.state_kind
    assert refuted_row.state_name == lapsed_row.state_name
    assert type(parse_criterion_evidence(refuted_row.body)) is type(
        parse_criterion_evidence(lapsed_row.body)
    )
    assert without_evidence(refuted_row.body) == without_evidence(lapsed_row.body)
    assert [row.issue_key for row in refuted_closure.gap(LANE)] == [
        row.issue_key for row in lapsed_closure.gap(LANE)
    ]
    assert [event.subject_key for event in refutations(refuted_port)] == [LANE_CHECK]
    assert refutations(lapsed_port) == []
