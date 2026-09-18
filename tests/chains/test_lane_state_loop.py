"""What a lane's own commits leave on its issue, driven through the real loop."""

import ast
import json
from pathlib import Path

import pytest
import structlog.testing

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.core.protocols import LaneStateWriter
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.criterion_cross_off import (
    UNDEMONSTRATED_REASON,
    evaluation_observation,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import TransientAPIError
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, render_lane_record
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import ResultEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import BranchRole, trunk_base
from kodezart.types.domain.criterion_lifecycle import CrossOffState
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.run_event import (
    RUN_EVENT_PUBLISHERS,
    RunEventKind,
    RunEventPublisher,
)
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    NATIVE_SESSION,
    OWED_KEYS,
    STAGE_KEY,
    SUBJECT,
    TRUNK_SHA,
    NativeExecutor,
    board,
    criterion_body,
    engine,
    native_evaluation,
    native_operation,
    tracker,
)
from tests.domain.test_criterion_cross_off import callers_of
from tests.fakes import make_tracker_issue
from tests.lane_fixture import (
    ADDED_OWED,
    LaneGit,
    LanePersister,
    LaneRepo,
    LaneSource,
    LosingBoard,
    added_criterion,
    criteria_echo,
    lane_forge,
)

BRANCH = "ralph/fire-subject"
FEATURE = "feature/fire-subject"
#: The run whose surface this lane records against, and the prompt cache it
#: happens to reuse: two seats of the context, so a record naming the wrong
#: one names a value no reader of this lane's record can follow.
JOB = "actual-parent-job"
CACHE = "actual-parent-cache"
REPO_URL = "https://github.com/owner/repo"
#: The source tree a static guard over the write site derives itself from.
SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
LOOP = SOURCE_ROOT / "chains" / "ralph_loop.py"
#: The subject this lane's fire is addressed to, as a scope of one issue.
SCOPE_OF_SUBJECT = ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
#: A local bare repository: an origin a forge API cannot be asked about.
FORGE_LESS_ORIGIN = "file:///srv/lanes/repo.git"


class CountingCriteria(TrackerCriteria):
    """The criteria reader, counting what a node asks it to read.

    A refusal owed at a node's entry is only visible against a reader that
    can say it was never asked: without the count, the same refusal raised
    after a board read would read exactly the same.
    """

    def __init__(self, *, tracker):
        super().__init__(tracker=tracker)
        self.current_reads = 0

    async def read_current(self, *, spec, held=None):
        self.current_reads += 1
        return await super().read_current(spec=spec, held=held)


class Lane:
    """One lane: its repository, its board, and the real loop over both."""

    def __init__(
        self,
        *,
        evaluations,
        forge=None,
        max_iterations=1,
        lane_operation=None,
        port=None,
        publishes=None,
        work_base_ref="main",
        resumed_head_sha=None,
        repo_url=REPO_URL,
        writes_lane_state=True,
        owns_workspace=True,
        source=LaneSource,
    ):
        self.work_base_ref = work_base_ref
        self.resumed_head_sha = resumed_head_sha
        self.repo_url = repo_url
        self.repo = LaneRepo(branch=BRANCH)
        self.git = LaneGit(self.repo)
        self.port = tracker() if port is None else port
        self.criteria = CountingCriteria(tracker=self.port)
        self.executor = NativeExecutor(evaluations)
        self.persister = LanePersister(self.repo, publishes=publishes)
        self.fire = engine(
            criteria=self.criteria,
            executor=self.executor,
            real_loop=True,
            max_iterations=max_iterations,
            persister=self.persister,
            git=self.git,
            source=source(self.repo),
            forge=forge,
            lane_operation=lane_operation,
            writes_lane_state=writes_lane_state,
            owns_workspace=owns_workspace,
        )
        self.loop = self.fire.implementation._quality_gate

    def graded_in(self) -> str:
        """The tree the last evaluation session was streamed in."""
        return self.executor.evaluation_workspaces[-1]

    def leave_changes_behind(self, count: int) -> None:
        """Let the evaluator session end with its tree holding changes."""
        self.git.dirtied.add(self.graded_in())

    def move_the_head(self, count: int) -> None:
        """Let the evaluator session end with its tree at another commit."""
        self.git.heads[self.graded_in()] = "0" * 40

    async def arguments(self):
        """Everything the loop is dispatched with, for one run of this lane.

        The two reads here are this fixture's own way in; what the node asks
        the reader for starts at zero afterwards.
        """
        spec = await self.criteria.read_spec(issue_key=SUBJECT)
        entry = await self.criteria.read_current(spec=spec)
        self.criteria.current_reads = 0
        return {
            "prompt": "Implement the current Checks.",
            "repo_path": None,
            "repo_url": self.repo_url,
            "feature_branch": FEATURE,
            "ralph_branch": BRANCH,
            "base_spec": trunk_base("main"),
            "work_base_ref": self.work_base_ref,
            "resumed_head_sha": self.resumed_head_sha,
            "permission_mode": PermissionMode.UNATTENDED,
            "allowed_tools": ToolPreset.IMPLEMENTATION,
            "acceptance_criteria": list(entry.criteria),
            "tracker_spec": spec,
            "cache_key": CACHE,
            "surface_holder": JOB,
            "repo_visibility": RepoVisibility.PUBLIC,
        }

    async def run(self, events=None):
        seen = [] if events is None else events
        async for event in self.loop.run(**await self.arguments()):
            seen.append(event)
        return seen

    async def record(self):
        _, record = await LaneRecordReader(
            tracker=self.port, operation=native_operation()
        ).read(issue_key=SUBJECT, lane_key=SUBJECT)
        return record

    def record_comments(self):
        prefix = native_operation().marker_prefixes["run_state"]
        return [
            comment
            for comment in self.port.comments
            if comment.body.startswith(f"[{prefix}:")
        ]


@pytest.mark.parametrize("clone", ["behind", "level"])
async def test_a_continued_branch_must_stand_at_the_head_the_lane_entered_on(clone):
    """The tree comes from the clone; the entry read the remote (KOD-684).

    A first iteration whose work base IS the loop branch continues an existing
    branch, and the tree it works in is cut from the clone's copy of it. A copy
    behind the head the entry decided on would hand the session commits the
    criteria this lane owes were already graded against, so the loop compares
    the two and refuses before the session, before any commit and before the
    record write a commit carries. Not vacuous: the same lane at the head it
    entered on runs and records.
    """
    lane = Lane(
        evaluations=[native_evaluation()],
        work_base_ref=BRANCH,
        resumed_head_sha="0" * 40 if clone == "behind" else TRUNK_SHA,
    )

    if clone == "behind":
        with pytest.raises(
            NativeWriteRefusalError, match="not at the head the lane entered on"
        ):
            await lane.run()
        assert lane.executor.execution_prompts == []
        assert lane.persister.calls == []
        assert lane.record_comments() == []
        return

    await lane.run()
    assert lane.executor.execution_prompts
    assert (await lane.record()).head_sha == lane.repo.head


async def test_a_continued_branch_with_no_entered_head_refuses() -> None:
    """The head is not optional where the branch already exists.

    A continued branch whose entry named no head is a lane nothing can compare
    the clone against, which is the state this refusal exists to make loud
    rather than to work around.
    """
    lane = Lane(evaluations=[native_evaluation()], work_base_ref=BRANCH)

    with pytest.raises(NativeWriteRefusalError, match="names no head to continue from"):
        await lane.run()

    assert lane.executor.execution_prompts == []
    assert lane.record_comments() == []


async def test_first_push_leaves_the_record_and_the_first_push_event():
    lane = Lane(evaluations=[native_evaluation()], forge=lane_forge())
    await lane.run()

    assert len(lane.record_comments()) == 1
    events = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [event.kind for event in events] == [RunEventKind.FIRST_PUSH]
    assert len(lane.port.comments) == 2

    record = await lane.record()
    assert record.lane_key == SUBJECT
    assert record.branch == BRANCH
    assert record.branch_url == f"{REPO_URL}/tree/{BRANCH}"
    assert record.head_sha == lane.repo.head
    assert record.pushed_head_sha == record.head_sha
    assert record.commits_ahead == 1
    assert record.files_changed == 1
    assert [(row.sha, row.subject, row.issue_id) for row in record.commits] == [
        (lane.repo.head, "feat: commit 1", SUBJECT)
    ]
    assert [
        (item.branch, item.role, item.derived_from, item.run_id)
        for item in record.associations
    ] == [
        (FEATURE, BranchRole.DELIVERABLE, "main", JOB),
        (BRANCH, BranchRole.LOOP, FEATURE, JOB),
    ]


async def test_a_forge_less_origin_is_recorded_at_the_address_it_is_reachable_at():
    """A forge in the wiring is not a forge behind this origin.

    A local bare repository has no branch page, and an address composed for
    it would read as one: the record carries the origin instead, and the
    forge that is wired is asked nothing about a repository it cannot hold.
    """
    lane = Lane(
        evaluations=[native_evaluation()],
        forge=lane_forge(),
        repo_url=FORGE_LESS_ORIGIN,
    )
    await lane.run()
    assert (await lane.record()).branch_url == FORGE_LESS_ORIGIN


async def test_a_round_built_on_earlier_work_records_the_runs_own_base():
    """Every recorded identity is read off the loop's context, one seat each.

    A round that continues earlier work cuts its loop branch from the
    deliverable branch while its scope is still measured against the base the
    run was dispatched on, so those two refs differ here. The record has to
    carry the second, and each branch has to carry its own role: a pair of
    seats exchanged where the binding is built would send a re-entering
    reader to the wrong branch and grade it against the work it contains.
    """
    lane = Lane(evaluations=[native_evaluation()], work_base_ref=FEATURE)
    await lane.run()

    record = await lane.record()
    assert (record.lane_key, record.branch) == (SUBJECT, BRANCH)
    assert [
        (item.branch, item.role, item.derived_from, item.run_id)
        for item in record.associations
    ] == [
        (FEATURE, BranchRole.DELIVERABLE, "main", JOB),
        (BRANCH, BranchRole.LOOP, FEATURE, JOB),
    ]


@pytest.mark.parametrize("missing", [RUN_STATE_PURPOSE, RUN_EVENT_PURPOSE])
async def test_an_operation_missing_a_record_purpose_refuses_before_the_session(
    missing,
):
    """Either purpose the record write needs is settled at the node's entry.

    Both are knowable from the operation alone, so either absence costs no
    session, no criteria read and no commit; resolved one at a time, the
    second would be found after a push and after a comment.
    """
    port = tracker()
    lane = Lane(
        evaluations=[native_evaluation()],
        port=port,
        lane_operation=OperationConfig(
            operation_name="native-fixture",
            workspace="fixture",
            marker_prefixes={
                purpose: prefix
                for purpose, prefix in native_operation().marker_prefixes.items()
                if purpose != missing
            },
            issue_labels={"decision": "decision"},
        ),
    )

    with pytest.raises(OperationMemberAbsentError, match=missing):
        await lane.run()

    assert lane.criteria.current_reads == 0
    assert lane.executor.execution_prompts == []
    assert lane.persister.calls == []
    assert lane.repo.shas == []
    assert port.comments == []


async def test_a_native_iteration_with_no_record_writer_refuses_before_any_read():
    """A native loop that cannot record its commit refuses at the node's entry.

    The writer's absence is knowable from the wiring, so the refusal names
    the collaborator it lacks and the criteria reader is never asked: raised
    after that read, it would cost a board round trip to say what the node
    knew before it started.
    """
    lane = Lane(evaluations=[native_evaluation()], writes_lane_state=False)

    with pytest.raises(NativeWriteRefusalError, match="lane state writer"):
        await lane.run()

    assert lane.criteria.current_reads == 0
    assert lane.executor.execution_prompts == []
    assert lane.persister.calls == []
    assert lane.repo.shas == []
    assert lane.port.comments == []


async def test_a_native_iteration_with_no_workspace_provider_refuses_before_any_read():
    """The tree a native verdict is about is wiring, settled at the entry.

    A loop with no provider cannot own the tree its evaluation is graded in,
    and that is knowable from the wiring alone. Refused at the evaluate
    node's own entry it would already have opened the implementation
    session, committed and pushed; refused here it costs a board round trip,
    a session and a commit less.
    """
    lane = Lane(evaluations=[native_evaluation()], owns_workspace=False)

    with pytest.raises(NativeWriteRefusalError, match="workspace provider"):
        await lane.run()

    assert lane.criteria.current_reads == 0
    assert lane.executor.execution_prompts == []
    assert lane.persister.calls == []
    assert lane.repo.shas == []
    assert lane.port.comments == []


async def test_a_second_commit_edits_the_record_and_posts_no_second_event():
    lane = Lane(
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=2,
    )
    await lane.run()

    assert len(lane.repo.shas) == 2
    assert len(lane.record_comments()) == 1
    assert len(lane.port.comments) == 2
    record = await lane.record()
    assert [row.sha for row in record.commits] == lane.repo.shas
    assert record.head_sha == lane.repo.head
    assert [item.role for item in record.associations] == [
        BranchRole.DELIVERABLE,
        BranchRole.LOOP,
    ]


def board_bodies(port) -> list[tuple[str, str]]:
    """The board as bytes: each comment's own key and the text it holds."""
    return [(comment.comment_key, comment.body) for comment in port.comments]


def recorded_payload(body: str) -> dict:
    """The record's own JSON block, read off the stored comment."""
    return json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])


async def test_a_lane_killed_during_an_evaluation_is_located_from_the_tracker_alone():
    lane = Lane(
        evaluations=[
            native_evaluation(failed=True),
            native_evaluation(failed=True),
            native_evaluation(),
        ],
        max_iterations=3,
        # Only the second commit reaches the remote, so at the kill the pushed
        # head is neither the current head nor what the first record recorded:
        # a record carrying its own earlier value forward would read the same
        # as one that observed the remote, and neither would be shown.
        publishes=lambda commits: commits == 2,
    )
    port = lane.port
    at_die: list[list[tuple[str, str]]] = []

    def die(evaluation: int) -> None:
        if evaluation == 3:
            at_die.append(board_bodies(port))
            raise ConnectionResetError("the lane was killed mid-loop")

    lane.executor.on_evaluation = die
    events: list[object] = []
    with pytest.raises(ConnectionResetError):
        await lane.run(events)

    at_kill = (lane.repo.head, lane.repo.pushed, tuple(lane.repo.shas))
    streamed = [
        event.commit_sha
        for event in events
        if isinstance(event, ResultEvent) and event.commit_sha
    ]
    # Everything the run held is dropped: only the board survives the kill.
    del lane

    # Nothing on the way out completed or repaired the record: the board the
    # kill instant held is the board this read is answered from.
    assert board_bodies(port) == at_die[0]

    comment, record = await LaneRecordReader(
        tracker=port, operation=native_operation()
    ).read(issue_key=SUBJECT, lane_key=SUBJECT)
    assert comment.issue_key == SUBJECT
    assert record.branch == BRANCH
    assert record.head_sha == at_kill[0]
    assert record.pushed_head_sha == at_kill[1]
    assert record.pushed_head_sha != record.head_sha
    assert [row.sha for row in record.commits] == list(at_kill[2])
    assert len(record.commits) == 3
    assert record.pr is None
    assert recorded_payload(comment.body)["pr"] is None
    assert [row.sha for row in record.commits] == streamed


async def test_a_killed_lane_keeps_the_pull_request_its_record_already_carried():
    port = tracker()
    carried = LaneRunState.model_validate(
        {
            "laneKey": SUBJECT,
            "branch": BRANCH,
            "branchUrl": REPO_URL,
            "headSha": "0" * 40,
            "pushedHeadSha": "0" * 40,
            "commitsAhead": 1,
            "filesChanged": 1,
            "commits": [{"sha": "0" * 40, "subject": "earlier", "issueId": SUBJECT}],
            "pr": {
                "url": f"{REPO_URL}/pull/17",
                "number": 17,
                "state": "OPEN",
            },
            "associations": [
                {
                    "branch": FEATURE,
                    "role": "deliverable",
                    "derivedFrom": "main",
                    "runId": JOB,
                },
                {
                    "branch": BRANCH,
                    "role": "loop",
                    "derivedFrom": FEATURE,
                    "runId": JOB,
                },
            ],
        }
    )
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_lane_record(
            record=carried, marker_prefixes=native_operation().marker_prefixes
        ),
    )
    lane = Lane(
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=3,
        port=port,
    )

    at_die: list[list[tuple[str, str]]] = []

    def die(evaluation: int) -> None:
        if evaluation == 2:
            at_die.append(board_bodies(port))
            raise ConnectionResetError("the lane was killed mid-loop")

    lane.executor.on_evaluation = die
    with pytest.raises(ConnectionResetError):
        await lane.run()
    at_kill = (lane.repo.head, tuple(lane.repo.shas))
    del lane

    assert board_bodies(port) == at_die[0]
    comment, record = await LaneRecordReader(
        tracker=port, operation=native_operation()
    ).read(issue_key=SUBJECT, lane_key=SUBJECT)
    # The lane rewrote the record twice over the seeded one, so what stands is
    # its own work: the pull request survived those writes rather than their
    # absence.
    assert record.head_sha == at_kill[0]
    assert [row.sha for row in record.commits] == ["0" * 40, *at_kill[1]]
    assert record.pr == carried.pr
    assert recorded_payload(comment.body)["pr"] == {
        "url": f"{REPO_URL}/pull/17",
        "number": 17,
        "state": "OPEN",
    }


async def test_a_lane_killed_between_its_push_and_its_record_write_reads_one_behind():
    port = tracker()
    prefix = native_operation().marker_prefixes["run_state"]
    upsert, written = port.upsert_comment, []

    at_die: list[list[tuple[str, str]]] = []

    async def kill_the_second_record_write(*, marker: str, **rest):
        if marker.startswith(f"[{prefix}:"):
            written.append(marker)
            if len(written) == 2:
                at_die.append(board_bodies(port))
                raise ConnectionResetError("the lane was killed after its push")
        return await upsert(marker=marker, **rest)

    port.upsert_comment = kill_the_second_record_write
    lane = Lane(
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=3,
        port=port,
    )
    with pytest.raises(ConnectionResetError):
        await lane.run()

    at_kill = (lane.repo.head, lane.repo.pushed, tuple(lane.repo.shas))
    del lane

    assert board_bodies(port) == at_die[0]
    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=SUBJECT, lane_key=SUBJECT
    )
    # The branch is still the one the record names, and the repository is
    # pushed at its second commit; the record was killed before it could say
    # so, and states the first commit as both its head and its pushed head.
    assert record.branch == BRANCH
    assert at_kill[2] == (record.head_sha, at_kill[0])
    assert at_kill[1] == at_kill[0]
    assert record.head_sha != at_kill[0]
    assert record.pushed_head_sha == record.head_sha
    assert [row.sha for row in record.commits] == [record.head_sha]


def graded(passed, keys=OWED_KEYS) -> dict:
    """The shared echo builder over this module's own roster by default."""
    return criteria_echo(keys=keys, passed=passed)


def closure(port) -> SubtreeClosure:
    """The rollup a walker reads a subject's finished state from."""
    return SubtreeClosure(facts=dict(port.issues), ref=SCOPE_OF_SUBJECT)


def completed(port) -> set[str]:
    return {
        key
        for key in OWED_KEYS
        if port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    }


def states(port) -> dict[str, WorkflowStateKind]:
    """Every issue on the board by the state a cross-off could move it to."""
    return {key: issue.state_kind for key, issue in port.issues.items()}


async def test_cross_offs_appear_on_the_tracker_between_iterations():
    """The board carries iteration n's cross-offs while the loop still runs.

    Read off the fake tracker at the instant the iteration event arrives,
    never off what the loop returns: a consumer that sees the event can go to
    the board and find exactly the criteria that iteration passed already
    moved, with the rest still owed and the subject untouched. The changed
    keys are taken over the WHOLE board rather than over the owed roster, so
    a state moved anywhere else would show up here as well.
    """
    first = {DIRECT_OWED}
    lane = Lane(
        evaluations=[graded(first), graded(OWED_KEYS)],
        max_iterations=2,
    )
    subject_before = (
        lane.port.issues[SUBJECT].state_name,
        lane.port.issues[SUBJECT].body,
    )
    before_states = states(lane.port)
    observed: dict[int, tuple[set[str], set[str], bool, tuple[str, str]]] = {}
    pointers: dict[int, set[str]] = {}
    events: list[object] = []

    async for event in lane.loop.run(**await lane.arguments()):
        events.append(event)
        if isinstance(event, WorkflowIterationEvent):
            moved = states(lane.port)
            pointers[event.iteration] = {
                parse_criterion_evidence(lane.port.issues[key].body).test
                for key in completed(lane.port)
            }
            observed[event.iteration] = (
                {key for key, kind in moved.items() if kind != before_states[key]},
                completed(lane.port),
                closure(lane.port).is_closed(SUBJECT),
                (
                    lane.port.issues[SUBJECT].state_name,
                    lane.port.issues[SUBJECT].body,
                ),
            )

    assert observed[1] == (first, first, False, subject_before)
    assert observed[2] == (set(OWED_KEYS), set(OWED_KEYS), True, subject_before)
    assert [
        event.verdict for event in events if isinstance(event, WorkflowIterationEvent)
    ] == [
        AcceptVerdict.rejected,
        AcceptVerdict.accepted,
    ]
    assert SUBJECT not in {key for key, _ in lane.port.workflow_writes}
    assert SUBJECT not in {key for key, _, _ in lane.port.issue_writes}
    # Every cross-off carries the sha the evaluator's workspace was graded at.
    assert {
        parse_criterion_evidence(lane.port.issues[key].body).graded_sha
        for key in OWED_KEYS
    } == {await LaneSource(lane.repo).resolve_commit(cwd="/w", ref=BRANCH)}
    # And the grading it came from: the session the evaluator ran in and the
    # iteration that ran it, read at the instant each iteration's event went
    # out, so a row pointing at no session or at another one is not this one.
    assert pointers[1] == {
        evaluation_observation(session_id=NATIVE_SESSION, iteration=1)
    }
    assert pointers[2] == {
        evaluation_observation(session_id=NATIVE_SESSION, iteration=2)
    }


async def test_a_criterion_added_between_iterations_is_graded_and_crossed_off():
    """What this loop itself grades stays inside the set it is judged against.

    The obligation can grow mid-run — an amendment write-back puts a
    criterion back in Todo, or the board gains one — and the next iteration
    owes it. Once this loop's own evaluation has graded it and the cross-off
    has moved it out of Todo, the roster has to hold it, or the check after
    the loop would read a set short of exactly the criterion the loop
    finished and refuse a lane with all its work done.
    """
    lane = Lane(
        evaluations=[
            graded({DIRECT_OWED}),
            graded({*OWED_KEYS, ADDED_OWED}, keys=(*OWED_KEYS, ADDED_OWED)),
        ],
        max_iterations=2,
    )
    lane.executor.on_evaluation = lambda count: (
        added_criterion(lane.port, ADDED_OWED) if count == 1 else None
    )

    events = await lane.run()

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert [event.verdict for event in iterations] == [
        AcceptVerdict.rejected,
        AcceptVerdict.accepted,
    ]
    assert {
        result.criterion_id for result in iterations[-1].evaluation.criteria_results
    } == {*OWED_KEYS, ADDED_OWED}
    finished = {*OWED_KEYS, ADDED_OWED}
    assert {
        key
        for key in finished
        if lane.port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    } == finished
    assert {
        parse_criterion_evidence(lane.port.issues[key].body).graded_sha
        for key in finished
    } == {lane.repo.head}


async def test_a_criterion_crossed_off_mid_run_is_still_graded_by_later_iterations():
    """The roster the EVALUATION reads is the one the loop is judged against.

    A criterion the subtree gained mid-run is graded, crossed off, and then
    has to stay in every later iteration's dispatch: read with the entry
    roster alone it would be Done and outside the set, so nothing would
    re-grade it and a regression of it after its cross-off would be absorbed
    while the lane still delivered. Three iterations are what shows it —
    the added criterion is finished on the second and has to be graded again
    on the third.
    """
    everything = (*OWED_KEYS, ADDED_OWED)
    lane = Lane(
        evaluations=[
            graded({DIRECT_OWED}),
            graded({ADDED_OWED}, keys=everything),
            graded(everything, keys=everything),
        ],
        max_iterations=3,
    )
    lane.executor.on_evaluation = lambda count: (
        added_criterion(lane.port, ADDED_OWED) if count == 1 else None
    )

    events = await lane.run()

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iterations) == 3
    assert {
        result.criterion_id for result in iterations[-1].evaluation.criteria_results
    } == set(everything)
    assert iterations[-1].fan_in is None
    assert [event.verdict for event in iterations] == [
        AcceptVerdict.rejected,
        AcceptVerdict.rejected,
        AcceptVerdict.accepted,
    ]
    assert {
        key
        for key in everything
        if lane.port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    } == set(everything)


async def test_a_criterion_reset_to_todo_mid_run_is_graded_and_finished():
    """The amendment case: a criterion outside the entry roster, put back.

    A criterion finished before the fire entered is in no roster, so nothing
    this loop does touches it — until a write-back moves it back to Todo,
    which puts it into the next barrier's own reading of what is owed. The
    loop grades it, finishes it at the head it graded, and the check after
    the loop accepts the lane it finished.
    """
    lane = Lane(
        evaluations=[
            graded({DIRECT_OWED}),
            graded({*OWED_KEYS, DIRECT_DONE}, keys=(*OWED_KEYS, DIRECT_DONE)),
        ],
        max_iterations=2,
    )

    async def reopen(count: int) -> None:
        """What a write-back does to a criterion it amended: put it back."""
        if count == 1:
            await lane.port.reset_criterion_pending(
                expected=lane.port.issues[DIRECT_DONE], holder=None
            )

    lane.executor.on_evaluation = reopen

    seen = await lane.run()

    iterations = [e for e in seen if isinstance(e, WorkflowIterationEvent)]
    assert [event.verdict for event in iterations] == [
        AcceptVerdict.rejected,
        AcceptVerdict.accepted,
    ]
    assert lane.port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.COMPLETED
    assert (
        parse_criterion_evidence(lane.port.issues[DIRECT_DONE].body).graded_sha
        == lane.repo.head
    )


async def test_a_criterion_added_after_the_last_evaluation_refuses_the_lane():
    """A set that changed since the last grading is what the check is for.

    The criterion appears after the only evaluation of the run, so no
    grading of this loop's ever covered it. The cross-offs that evaluation
    did produce stand on the board, and the check after the loop refuses
    rather than letting a judgment stand over a roster it never read.
    """
    lane = Lane(evaluations=[graded(OWED_KEYS)])
    lane.executor.on_evaluation = lambda _: added_criterion(lane.port, ADDED_OWED)

    with pytest.raises(NativeWriteRefusalError, match="Current Checks differ"):
        await lane.run()

    assert completed(lane.port) == set(OWED_KEYS)
    assert lane.port.issues[ADDED_OWED].state_kind is WorkflowStateKind.UNSTARTED


def recording(lane) -> list[tuple[CrossOffState, ...]]:
    """Every whole verdict handed to the lane's writer, as its states."""
    states: list[tuple[CrossOffState, ...]] = []
    writer = lane.loop._lane_state
    written = writer.write_cross_offs

    async def observed(*, lane, dispatched, cross_offs):
        states.append(tuple(cross_off.state for cross_off in cross_offs))
        await written(lane=lane, dispatched=dispatched, cross_offs=cross_offs)

    writer.write_cross_offs = observed
    return states


#: The tree the loop resolves the branch in: never the one it grades in.
CACHE_PATH = "/tmp/fake-cache"


def releases(lane) -> list[int]:
    """How many git calls this lane had made at each workspace release.

    The double hands the same path back after a release and keeps answering
    for it, so nothing in it distinguishes a read taken while the tree was
    owned from one taken after it was given up. The moment is what does:
    a read recorded after the last release is a read of a tree the loop no
    longer holds.
    """
    at_release: list[int] = []
    provider = lane.loop._workspace
    released = provider.release

    async def observed(workspace_path: str) -> None:
        at_release.append(len(lane.git.calls))
        await released(workspace_path)

    provider.release = observed
    return at_release


@pytest.mark.parametrize(
    "left_behind",
    [
        pytest.param(Lane.leave_changes_behind, id="uncommitted-change"),
        pytest.param(Lane.move_the_head, id="head-elsewhere"),
    ],
)
async def test_a_workspace_that_is_not_the_graded_sha_yields_no_cross_off(left_behind):
    """A verdict is only the branch's when the tree it read was the branch's.

    An uncommitted change in the grading workspace, or a head that is not the
    sha the verdict would be stamped with, means what the evaluator read was
    somebody's working copy. The evaluator session leaves it that way, in the
    tree it was streamed in and at the moment it ends, so both facts are read
    off THAT tree and after THAT session or they are read off nothing. Then:
    nothing is written to any sub-issue, every result carries the fixed
    reason in place of a verdict, the iteration is rejected, and the writer is
    handed the fourth state for each criterion.
    """
    lane = Lane(evaluations=[native_evaluation()])
    before = {
        key: (issue.state_name, issue.body) for key, issue in lane.port.issues.items()
    }
    states = recording(lane)
    at_session: list[int] = []
    at_release = releases(lane)

    def session_over(count: int) -> None:
        at_session.append(len(lane.git.calls))
        left_behind(lane, count)

    lane.executor.on_evaluation = session_over
    events = await lane.run()

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert [event.verdict for event in iterations] == [AcceptVerdict.rejected]
    assert {
        result.reasoning for result in iterations[-1].evaluation.criteria_results
    } == {UNDEMONSTRATED_REASON}
    assert states == [tuple(CrossOffState.undemonstrated for _ in OWED_KEYS)]
    assert {
        key: (issue.state_name, issue.body) for key, issue in lane.port.issues.items()
    } == before
    assert lane.port.workflow_writes == []
    assert lane.port.issue_writes == []
    # Both facts name the tree the evaluator was streamed in, and both are
    # read after that stream ended: read before it, or off the tree the
    # branch was resolved in, they would be facts about another tree.
    graded_in = lane.executor.evaluation_workspaces[0]
    assert graded_in != CACHE_PATH
    dirt_reads = [
        index
        for index, call in enumerate(lane.git.calls)
        if call == ("has_changes", graded_in)
    ]
    head_reads = [
        index
        for index, call in enumerate(lane.git.calls)
        if call == ("current_sha", graded_in)
    ]
    assert dirt_reads == [index for index in dirt_reads if index >= at_session[0]]
    assert len(dirt_reads) == 1
    post_session_heads = [index for index in head_reads if index >= at_session[0]]
    assert post_session_heads
    assert ("has_changes", CACHE_PATH) not in lane.git.calls
    assert ("current_sha", CACHE_PATH) not in lane.git.calls
    # And both are read while the loop still owns that tree: the evaluator's
    # workspace is the last one released, and a fact read after that release
    # is a fact about a tree this loop had already given up.
    assert at_release
    assert dirt_reads[0] < at_release[-1]
    assert post_session_heads[0] < at_release[-1]


async def test_an_undemonstrated_grading_says_so_in_the_lanes_log():
    """The third leg of "recorded": the harness's own reading, once.

    The typed value reaches the writer and the fixed reason reaches the
    iteration event; this line is what a person reading the run's log finds,
    and it names the sha the verdict would have been stamped with.
    """
    lane = Lane(evaluations=[native_evaluation()])
    lane.executor.on_evaluation = lane.leave_changes_behind

    with structlog.testing.capture_logs() as logs:
        await lane.run()

    undemonstrated = [
        entry for entry in logs if entry.get("event") == "evaluation_undemonstrated"
    ]
    assert [(entry["graded_sha"], entry["iteration"]) for entry in undemonstrated] == [
        (lane.repo.head, 1)
    ]


async def test_a_clean_workspace_at_the_graded_sha_is_what_a_cross_off_needs():
    """The same lane, demonstrated: the fourth state is not the only outcome."""
    lane = Lane(evaluations=[native_evaluation()])
    states = recording(lane)

    await lane.run()

    assert states == [tuple(CrossOffState.passed for _ in OWED_KEYS)]
    assert completed(lane.port) == set(OWED_KEYS)


async def test_the_evaluation_is_graded_in_a_workspace_the_loop_owns():
    """The tree a verdict is about is acquired at its sha and then released.

    The sha, not the branch name: a workspace acquired at the branch would
    follow the branch, and the two facts read off it afterwards would then be
    read from whatever the branch had become.
    """
    lane = Lane(evaluations=[native_evaluation()])
    provider = lane.loop._workspace

    await lane.run()

    acquired = [call[2] for call in provider.calls if call[0] == "acquire"]
    assert lane.repo.head in acquired
    assert BRANCH not in acquired
    assert [call[0] for call in provider.calls].count("release") == len(acquired)


def written(port) -> tuple[int, int]:
    """How much this board has been written: state moves and body edits.

    Those two only, and the blind spot is the move back out of the finished
    state: the double answers ``reset_criterion_pending`` without a
    ``workflow_writes`` entry and without an ``issue_writes`` one, so a
    move back made after the loop would leave this count where it was. What
    would show it is the completed-set assertion beside this one, read off
    the board's own states, and the comment count, which a refutation's
    event would move.
    """
    return len(port.workflow_writes), len(port.issue_writes)


def at_loop_exit(lane, seen: list[tuple[int, int]]):
    """Record how much the board holds at the instant the loop's stream ends.

    The measurement point the Check asks for: an exit that emitted no
    iteration event has nothing for a per-event snapshot to read, and the
    steps after the loop run either way.
    """
    run = lane.loop.run

    def observed(**arguments):
        async def stream():
            async for event in run(**arguments):
                yield event
            seen.append(written(lane.port))

        return stream()

    lane.loop.run = observed


@pytest.mark.parametrize(
    "fixture,crossed_off",
    [
        pytest.param(
            # One for the loop's iteration, one for the post-merge review.
            {"evaluations": [native_evaluation(), native_evaluation()]},
            set(OWED_KEYS),
            id="accepted",
        ),
        pytest.param(
            {"evaluations": [native_evaluation(failed=True)]},
            set(),
            id="rejected-at-the-cap",
        ),
        pytest.param(
            {
                "evaluations": [native_evaluation(failed=True) for _ in range(3)],
                "max_iterations": 3,
            },
            set(),
            id="plateaued",
        ),
    ],
)
async def test_no_step_after_the_loop_writes_a_cross_off(fixture, crossed_off):
    """Whatever the loop ends as, the cross-offs are all it left behind.

    The whole fire is driven, so consolidation, the post-merge review and the
    terminal step all run after the loop. What the board has been written is
    counted when the loop's own stream ends and again at the end of the fire,
    and the two are equal: the terminal aggregates and reports and is the
    first writer of nothing. The per-event snapshot is the same observation
    asked one step earlier, and applies to the exits that emitted an
    iteration event.
    """
    lane = Lane(**fixture)
    at_last_iteration: list[tuple[int, int]] = []
    at_exit: list[tuple[int, int]] = []
    at_loop_exit(lane, at_exit)

    async for event in lane.fire.run(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url=REPO_URL,
        base_spec=trunk_base("main"),
        scope=SCOPE_OF_SUBJECT,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
        cache_key=CACHE,
    ):
        if isinstance(event, WorkflowIterationEvent):
            at_last_iteration.append(written(lane.port))

    assert at_exit == [written(lane.port)]
    if at_last_iteration:
        assert written(lane.port) == at_last_iteration[-1]
    # Not vacuous: the accepted fixture did write cross-offs, and they were
    # already there when its last iteration event went out.
    assert completed(lane.port) == crossed_off
    assert (written(lane.port) != (0, 0)) is bool(crossed_off)


def test_the_evaluator_step_is_the_only_caller_of_write_cross_offs():
    """The write site is inside the loop's evaluator step and nowhere else.

    What the guard covers: every ``.py`` file under ``src/kodezart/``, parsed,
    looking for calls named after the port member and after the loop's own
    private that makes them — both taken from the code rather than spelled
    here, so renaming either moves the guard with it.

    What it does not see, and what review has to read from the code: a call
    reached by reflection, and a second writer that reproduces the two tracker
    calls a cross-off is made of instead of calling this member. The latter is
    covered from the other side by the guards over the Evidence application
    and the finished-state move, which no such writer could avoid.
    """
    sources = {
        path: ast.parse(path.read_text()) for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }
    member = LaneStateWriter.write_cross_offs.__name__
    private = RalphLoop._cross_off.__name__
    node = RalphLoop._evaluate_node.__name__

    assert {
        path: found
        for path, found in (
            (path, callers_of(tree, name=member)) for path, tree in sources.items()
        )
        if found
    } == {LOOP: [f"{RalphLoop.__name__}.{private}"]}
    assert {
        path: found
        for path, found in (
            (path, callers_of(tree, name=private)) for path, tree in sources.items()
        )
        if found
    } == {LOOP: [f"{RalphLoop.__name__}.{node}"]}


#: A criterion set long enough that a per-criterion comment would be visible
#: against the two the lane posts for itself.
WIDE_CRITERIA = tuple(f"{SUBJECT}/check-{index}" for index in range(8))


def wide_board(keys=WIDE_CRITERIA):
    """The subject with as many criterion sub-issues as *keys* names."""
    return board(
        [
            make_tracker_issue(
                SUBJECT,
                issue_labels=frozenset({STAGE_KEY}),
                body="the subject's own text",
            ),
            *(
                make_tracker_issue(
                    key,
                    parent_key=SUBJECT,
                    issue_labels=frozenset({"criterion"}),
                    body=criterion_body(key),
                )
                for key in keys
            ),
        ]
    )


def wide_evaluation(passing) -> dict:
    """The same echo builder over the long set."""
    return criteria_echo(keys=WIDE_CRITERIA, passed=passing)


async def test_a_long_criterion_set_over_many_iterations_posts_only_vocabulary_events():
    """Five iterations move five criteria and add no comment between them.

    The lane's own two comments are the record it rewrites in place and the
    one event it posts. A per-criterion state move is not an event, so eight
    criteria over five iterations leave the comment count where the first
    push left it, and no criterion sub-issue is commented on at all.
    """
    port = wide_board()
    lane = Lane(
        port=port,
        evaluations=[wide_evaluation(WIDE_CRITERIA[: index + 1]) for index in range(5)],
        max_iterations=5,
    )

    events = await lane.run()

    assert len([e for e in events if isinstance(e, WorkflowIterationEvent)]) == 5
    assert {
        key
        for key in WIDE_CRITERIA
        if port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    } == set(WIDE_CRITERIA[:5])
    # The count the comment count is measured against: five moves, one per
    # criterion the five iterations passed, and two comments throughout.
    assert len(port.workflow_writes) == 5
    assert len(port.comments) == 2
    assert {comment.issue_key for comment in port.comments} == {SUBJECT}
    posted = await port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [event.kind for event in posted] == [RunEventKind.FIRST_PUSH]
    # A lane's own stream carries only the kinds the lane publishes; a kind
    # some other raiser owns would be somebody else's write on this log.
    assert {RUN_EVENT_PUBLISHERS[event.kind] for event in posted} == {
        RunEventPublisher.LANE
    }
    assert len(posted) + len(lane.record_comments()) == len(port.comments)


async def test_a_regression_inside_the_loop_moves_the_criterion_back_and_says_so():
    """An iteration that breaks what an earlier one passed is not absorbed.

    Iteration 1 finishes two criteria and leaves one owed, so the loop runs
    again. Iteration 2 breaks one of the two: that sub-issue goes back out of
    its finished state carrying the refuting grading, and the lane's stream
    gains exactly one refutation keyed to it. Nothing writes the subject
    itself, and the rollup over its criteria answers for it throughout.
    """
    broken, kept, owed = OWED_KEYS
    lane = Lane(
        evaluations=[graded({broken, kept}), graded({kept})],
        max_iterations=2,
    )
    subject_before = (
        lane.port.issues[SUBJECT].state_name,
        lane.port.issues[SUBJECT].body,
    )
    observed: dict[int, tuple[set[str], bool]] = {}

    async for event in lane.loop.run(**await lane.arguments()):
        if isinstance(event, WorkflowIterationEvent):
            observed[event.iteration] = (
                completed(lane.port),
                closure(lane.port).is_closed(SUBJECT),
            )

    assert observed[1] == ({broken, kept}, False)
    assert observed[2] == ({kept}, False)
    assert lane.port.issues[owed].state_kind is WorkflowStateKind.UNSTARTED
    assert lane.port.issues[broken].state_kind is WorkflowStateKind.UNSTARTED
    assert (
        parse_criterion_evidence(lane.port.issues[broken].body).graded_sha
        == lane.repo.head
    )
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        event.subject_key
        for event in posted
        if event.kind is RunEventKind.CRITERION_REFUTED
    ] == [broken]
    assert (
        lane.port.issues[SUBJECT].state_name,
        lane.port.issues[SUBJECT].body,
    ) == subject_before
    assert SUBJECT not in {key for key, _, _ in lane.port.issue_writes}
    assert len(lane.port.comments) == 3


def losing_board() -> LosingBoard:
    """The lane's own board, ready to lose one named write."""
    source = tracker()
    return LosingBoard(
        issues=list(source.issues.values()),
        criteria_stage_label_key=STAGE_KEY,
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members=source.scope_label_members,
    )


#: What a refutation the loop died inside leaves for the NEXT fire to read:
#: the state the sub-issue is in, which grading its Evidence row carries, and
#: whether that fire's own entry-shaped read still owes the criterion. Each of
#: the act's three writes is one row, because each leaves its own state.
LOST_REFUTATION_WRITES = {
    "reset_criterion_pending": (WorkflowStateKind.COMPLETED, 0, False),
    "edit_description": (WorkflowStateKind.UNSTARTED, 0, True),
    "post_run_event": (WorkflowStateKind.UNSTARTED, 1, True),
}


@pytest.mark.parametrize("drops", sorted(LOST_REFUTATION_WRITES))
async def test_a_refutation_the_loop_died_inside_certifies_no_failing_grading(drops):
    """A run that dies taking a criterion back leaves no false claim behind.

    The loop has no handler for a write that does not land, so the run ends
    there and nothing later in it revisits the criterion. The move back is
    therefore the first write: losing anything after it leaves the criterion
    unstarted, which the next fire's entry-shaped read owes again — carrying
    the earlier grading when the stamp was lost and the refuting one when the
    event was, and in neither case an event. Losing the move back itself
    leaves the pass it already was — true of the head that passed — and never
    the failing grading's sha under a finished state, which no later fire
    would re-grade.
    """
    state, graded_at, owed_again = LOST_REFUTATION_WRITES[drops]
    broken, kept, _ = OWED_KEYS
    port = losing_board()
    lane = Lane(
        evaluations=[graded({broken, kept}), graded({kept})],
        max_iterations=2,
        port=port,
    )
    # Armed once iteration 1's ticks and the first-push event have landed and
    # before iteration 2 writes anything, so the write it loses is one of the
    # three the refutation makes: the run reaches the failure with a criterion
    # this fire finished, which is the state the act starts from.
    lane.executor.on_evaluation = lambda count: port.lose(drops) if count == 2 else None

    with pytest.raises(TransientAPIError):
        await lane.run()

    assert lane.port.issues[broken].state_kind is state
    assert (
        parse_criterion_evidence(lane.port.issues[broken].body).graded_sha
        == lane.repo.shas[graded_at]
    )
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        event.kind for event in posted if event.kind is RunEventKind.CRITERION_REFUTED
    ] == []
    # What a new fire over the same subject owes, read the way its entry
    # barrier reads it: with no roster held, so nothing this run claimed.
    spec = await lane.criteria.read_spec(issue_key=SUBJECT)
    current = await lane.criteria.read_current(spec=spec)
    assert (broken in {criterion.id for criterion in current.criteria}) is owed_again
