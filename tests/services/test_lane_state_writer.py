"""The lane's record is one comment the committing act keeps current."""

import json
from collections.abc import Sequence

import pytest

from kodezart.domain.criterion_cross_off import (
    cross_offs_for,
    evaluation_observation,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    LaneRecordWriteError,
    StaleCommentWriteError,
    StaleWriteError,
    TransientAPIError,
)
from kodezart.domain.fire_spec import replace_criterion_fields
from kodezart.domain.lane_record import RUN_STATE_PURPOSE
from kodezart.domain.run_event_stream import (
    RUN_EVENT_PURPOSE,
    LaneRunEvent,
    lane_run_events,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneBinding
from kodezart.types.domain.tracker import TrackerComment, WorkflowStateKind
from tests.fakes import FakeTrackerPort, PassThroughGate, make_tracker_issue
from tests.lane_fixture import LaneGit, LaneRepo, lane_operation

LANE = "LANE-1"
#: The remote this lane's repository is on, named unlike the production
#: default: under that default the value the writer must take from its
#: configuration coincides with the one a hard-coded remote would use, and a
#: record read off a remote nobody configured would read as this lane's.
REMOTE = "fixture-remote"
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


class DroppingBoard(FakeTrackerPort):
    """A board that loses the first event post, after the record is written."""

    dropped = 0

    async def post_run_event(self, *, issue_key: str, event):
        if self.dropped == 0:
            self.dropped += 1
            raise TransientAPIError("the event post was lost")
        return await super().post_run_event(issue_key=issue_key, event=event)


def event_comments(port: FakeTrackerPort) -> list:
    prefix = lane_operation().marker_prefixes["run_event"]
    return [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}:")
    ]


async def test_an_event_lost_after_the_record_is_posted_by_the_next_commit():
    port = DroppingBoard(
        issues=[make_tracker_issue(LANE)],
        marker_prefixes=lane_operation().marker_prefixes,
    )
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


@pytest.mark.parametrize(
    "damage",
    [
        lambda body: body.replace(RunEventKind.LANE_DISPATCHED.value, "invented"),
        lambda body: body.removesuffix("\n```"),
    ],
)
async def test_a_damaged_event_stream_refuses_the_write_instead_of_escaping(damage):
    """The stream is read on the write path, so its faults are this write's.

    A parse failure here lands after the push, on every later commit of the
    lane's life; as the read error it is, it would reach the caller as a
    fault about nothing it can name, with a pushed commit behind it.
    """
    port, repo = board(), lane_repo()
    await port.post_run_event(
        issue_key=LANE,
        event=LaneRunEvent(kind=RunEventKind.LANE_DISPATCHED, lane_key=LANE),
    )
    stored = event_comments(port)[0]
    port.comments[port.comments.index(stored)] = stored.model_copy(
        update={"body": damage(stored.body)}
    )

    with pytest.raises(LaneRecordWriteError, match="event stream could not be read"):
        await make_commit(writer(port, repo), repo, 1)

    assert record_comments(port) == []


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


async def test_the_posted_event_carries_the_marker_and_the_codec_fields_alone():
    port, repo = board(), lane_repo()
    await make_commit(writer(port, repo), repo, 1)

    prefix = lane_operation().marker_prefixes["run_event"]
    marker, _, block = event_comments(port)[0].body.partition("\n")
    assert marker == f"[{prefix}:{LANE}]"
    assert block.startswith("```json\n")
    assert block.endswith("\n```")
    payload = json.loads(block[len("```json\n") : -len("\n```")])
    assert payload == {
        "kind": RunEventKind.FIRST_PUSH.value,
        "laneKey": LANE,
        "subjectKey": None,
    }
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


def criteria_board(*, bodies: dict[str, str] | None = None) -> FakeTrackerPort:
    """The lane, its criterion sub-issues, and one issue that is not a criterion."""
    overrides = bodies or {}
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, body="the lane's own text"),
            make_tracker_issue(f"{LANE}/child", parent_key=LANE, body="a plain child"),
            *(
                make_tracker_issue(
                    key,
                    parent_key=LANE,
                    issue_labels=frozenset({"criterion"}),
                    body=overrides.get(key, criterion_body(key)),
                )
                for key in CRITERIA
            ),
        ],
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


async def tick(
    lane_state,
    *,
    sha: str,
    keys: Sequence[str] = CRITERIA,
    failed: Sequence[str] = (),
    demonstrated: bool = True,
) -> None:
    """One attempt's whole verdict, written the way the evaluator writes it."""
    await lane_state.write_cross_offs(
        lane=binding(),
        dispatched=dispatched(keys),
        cross_offs=cross_offs_for(
            results=graded(keys, failed=failed),
            graded_sha=sha,
            observation=evaluation_observation(session_id="eval-session", iteration=1),
            demonstrated=demonstrated,
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
    lane_state = writer(port, lane_repo())
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


async def test_a_verdict_that_does_not_answer_the_dispatched_roster_writes_nothing():
    port = criteria_board()
    lane_state = writer(port, lane_repo())

    with pytest.raises(LaneRecordWriteError, match="dispatched criteria"):
        await lane_state.write_cross_offs(
            lane=binding(),
            dispatched=dispatched(),
            cross_offs=cross_offs_for(
                results=graded(CRITERIA[:2]),
                graded_sha="4" * 40,
                observation="evaluator session eval-session, iteration 1",
                demonstrated=True,
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
    assert parse_criterion_evidence(port.issues[addressed].body).graded_sha == "7" * 40
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
async def test_a_tick_on_a_sub_issue_that_moved_after_dispatch_writes_nothing(drift):
    """The sub-issue is read back through the port, never remembered.

    The verdict was reached against what the dispatch saw; the write asserts
    what the sub-issue holds now, and a sub-issue that moved in between
    takes no part of the write at all — not its body, not its state, and not
    the sub-issues the same attempt would have gone on to address.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    moved = CRITERIA[0]
    port.issues[moved] = port.issues[moved].model_copy(update=drift)
    before = board_shape(port)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="8" * 40)

    assert caught.value.target == moved
    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []


async def test_a_tick_on_a_sub_issue_carrying_two_evidence_rows_writes_nothing():
    """The row the stamp sets has to be one row, and that is knowable first.

    A body a person edited, or one rendered before the codec, can carry two
    Evidence rows. The field-scoped edit names no single row to set, so the
    fresh read refuses such a sub-issue the way it refuses every other body
    this verdict no longer addresses — before a byte of it is written, and
    not as an untyped failure after the grading session has already run.
    """
    ambiguous = CRITERIA[0]
    port = criteria_board(
        bodies={
            ambiguous: f"{criterion_body(ambiguous)}\n**Evidence:** an older row",
        }
    )
    lane_state = writer(port, lane_repo())
    before = board_shape(port)

    with pytest.raises(StaleWriteError) as caught:
        await tick(lane_state, sha="9" * 40)

    assert caught.value.target == ambiguous
    assert board_shape(port) == before
    assert port.issue_writes == []
    assert port.workflow_writes == []


def refutations(port: FakeTrackerPort) -> list[LaneRunEvent]:
    """The refutation events this lane's stream holds, in order."""
    return [
        event
        for event in lane_run_events(
            comments=port.comments,
            lane_key=LANE,
            marker_prefixes=lane_operation().marker_prefixes,
        )
        if event.kind is RunEventKind.CRITERION_REFUTED
    ]


async def test_a_criterion_this_fire_finished_and_then_broke_is_taken_back():
    """A regression is recorded, not absorbed, and recorded exactly once.

    The first attempt finishes the criterion; the second fails it at a later
    head. The sub-issue goes back to the unstarted state with the refuting
    grading on its Evidence row, and the stream carries one refutation keyed
    to that criterion. A third attempt failing it again finds it unstarted —
    nothing this fire still claims — and writes nothing more.
    """
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    broken = CRITERIA[0]

    await tick(lane_state, sha="1" * 40)
    await tick(lane_state, sha="2" * 40, failed=[broken])

    issue = port.issues[broken]
    assert issue.state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(issue.body).graded_sha == "2" * 40
    assert [event.subject_key for event in refutations(port)] == [broken]
    # The criteria the same attempt passed again are untouched by any of it.
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in CRITERIA[1:]
    )

    at_second = (board_shape(port), len(port.comments))
    await tick(lane_state, sha="2" * 40, failed=[broken])
    assert (board_shape(port), len(port.comments)) == at_second
    assert [event.subject_key for event in refutations(port)] == [broken]


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

    await tick(lane_state, sha="7" * 40, demonstrated=False)

    assert board_shape(port) == finished
    assert refutations(port) == []
