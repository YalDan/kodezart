"""What a lane's own commits leave on its issue, driven through the real loop."""

import ast
import json
from pathlib import Path

import pytest
import structlog.testing

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.core.protocols import LaneStateWriter
from kodezart.domain.agent import best_iteration_ref
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.criteria_grading import UNDEMONSTRATED_REASONS
from kodezart.domain.criterion_cross_off import (
    CARRIED_REASON,
    LAPSE_POINTER,
    LAPSE_REASON,
    evaluation_observation,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    GitSourceReadError,
    TransientAPIError,
    WorkspaceError,
)
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.domain.lane_entry import recorded_commit
from kodezart.domain.lane_record import (
    LANDING_ROW_SUBJECT,
    RUN_STATE_PURPOSE,
    render_lane_record,
)
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import ResultEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import BranchRole, trunk_base
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CrossOffState,
    RederivationClass,
    StickyClassError,
    UndemonstratedReason,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.lane_entry import ResumedLane
from kodezart.types.domain.operation import (
    LifecycleStage,
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_event import (
    RUN_EVENT_PUBLISHERS,
    RunEventKind,
    RunEventPublisher,
)
from kodezart.types.domain.run_state import LaneEscalation, LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.workflow import RalphLoopState
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    DIRECT_OWED_TOO,
    NATIVE_SESSION,
    NESTED_OWED,
    OWED_KEYS,
    RATE_LIMITED_BASE_READING,
    STAGE_KEY,
    SUBJECT,
    TRUNK_BRANCHES,
    TRUNK_SHA,
    NativeExecutor,
    board,
    check_of,
    criterion_body,
    engine,
    native_evaluation,
    native_operation,
    tracker,
)
from tests.domain.test_criterion_cross_off import callers_of
from tests.fakes import (
    FakeBranchMerger,
    FakeRefPublisher,
    FakeWorkspaceProvider,
    dispatched_checks,
    dispatched_ids,
    make_criteria,
    make_tracker_issue,
)
from tests.lane_fixture import (
    ADDED_OWED,
    BASE_COMMAND,
    FixtureTreeGit,
    LaneGit,
    LanePersister,
    LaneRepo,
    LaneSource,
    LosingBoard,
    added_criterion,
    base_echo,
    criteria_echo,
    lane_forge,
)
from tests.mutation_fixture import (
    MutationLaneFixture,
    run_named_check,
    write_fixture_tree,
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
        repo=None,
        publishes=None,
        work_base_ref="main",
        resumed_head_sha=None,
        repo_url=REPO_URL,
        writes_lane_state=True,
        raises_lapse_questions=True,
        owns_workspace=True,
        source=LaneSource,
        fan_in_max_attempts=1,
        mutation=None,
    ):
        self.work_base_ref = work_base_ref
        self.resumed_head_sha = resumed_head_sha
        self.repo_url = repo_url
        # A second run over a repository a first run left behind continues
        # that repository's commits, the way a resumed lane continues the
        # branch it pushed rather than starting one of its own.
        self.repo = LaneRepo(branch=BRANCH) if repo is None else repo
        #: The fixture trees this lane is graded in, when it is graded against
        #: real source: the trees and the checks that name them arrive as one
        #: value, and its absence is a lane that takes no mutation reading.
        self.mutation = mutation
        self.git = (
            LaneGit(self.repo)
            if mutation is None
            else FixtureTreeGit(self.repo, mutation.workspaces)
        )
        if mutation is not None:
            mutation.workspaces.read_through(self.git)
        self.port = tracker() if port is None else port
        self.criteria = CountingCriteria(tracker=self.port)
        self.executor = NativeExecutor(
            evaluations,
            evaluate_in=None if mutation is None else mutation.evaluate_in,
        )
        if mutation is not None:
            self.executor.on_mutation = mutation.remove_in
        self.persister = LanePersister(self.repo, publishes=publishes)
        self.fire = engine(
            criteria=self.criteria,
            executor=self.executor,
            real_loop=True,
            max_iterations=max_iterations,
            fan_in_max_attempts=fan_in_max_attempts,
            persister=self.persister,
            git=self.git,
            source=source(self.repo),
            forge=forge,
            lane_operation=lane_operation,
            writes_lane_state=writes_lane_state,
            raises_lapse_questions=raises_lapse_questions,
            owns_workspace=owns_workspace,
            workspace=None if mutation is None else mutation.workspaces,
            reads_mutation=mutation is not None,
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

        The entry read here is this fixture's own way in; what the node asks
        the reader for starts at zero afterwards.
        """
        spec, entry = await self.criteria.read_entry(issue_key=SUBJECT)
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
    """The record, the first-push event, and one grading entry per criterion.

    The stream carries a grading entry for every criterion the iteration
    crossed off, because that entry is what makes the stream the Evidence
    row's own write history: a row restamped by a passing grading and no
    entry naming it reads as a row pointing behind its last grading
    (KOD-506). They are named here rather than filtered out, so the comment
    count still says the lane wrote nothing besides its record and its
    stream.
    """
    lane = Lane(evaluations=[native_evaluation()], forge=lane_forge())
    await lane.run()

    assert len(lane.record_comments()) == 1
    events = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    # The first push, and then for each criterion the evaluation passed its
    # grading entry and the lane's own account of it, at the head it was
    # graded at.
    assert [(event.kind, event.subject_key) for event in events] == [
        (RunEventKind.FIRST_PUSH, None),
        *(
            (kind, key)
            for key in OWED_KEYS
            for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
        ),
    ]
    crossings = [
        event for event in events if event.kind is RunEventKind.ISSUE_CROSSED_OFF
    ]
    assert len(crossings) == len(OWED_KEYS)
    assert sorted(event.subject_key for event in crossings) == sorted(OWED_KEYS)
    assert {event.graded_sha for event in events[1:]} == {lane.repo.head}
    assert len(lane.port.comments) == 8

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
    """A second commit rewrites the one record and adds no second first-push.

    The stream gains what the second iteration graded and nothing else: the
    first iteration crossed nothing off, so every grading entry here is the
    second iteration's, one per criterion it finished (KOD-506).
    """
    lane = Lane(
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=2,
    )
    await lane.run()

    assert len(lane.repo.shas) == 2
    assert len(lane.record_comments()) == 1
    events = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [(event.kind, event.subject_key) for event in events] == [
        (RunEventKind.FIRST_PUSH, None),
        *(
            (kind, key)
            for key in OWED_KEYS
            for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
        ),
    ]
    # The record, the one first push, and one grading entry and one
    # crossing-off per criterion the second evaluation passed: the failed
    # first one announced nothing.
    assert len(lane.port.comments) == 8
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


#: What is still Todo once the first iteration below crossed one criterion off.
REMAINING = (DIRECT_OWED_TOO, NESTED_OWED)


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


async def test_a_fire_killed_between_iterations_resumes_on_criteria_still_todo_alone():
    """The second run owes exactly what the board says is still Todo (KOD-400).

    Run one is killed at the first session of its second iteration, after
    the first iteration's cross-off landed and its commit was pushed.
    Nothing of it survives but the board and the repository.  Run two is a
    fresh engine over both, continuing the loop branch at the pushed head.
    Its dispatched roster is the Todo set and nothing else, it opens one
    session, and the criterion run one finished carries run one's grading,
    byte for byte, when run two is over.
    """
    first = Lane(
        evaluations=[graded({DIRECT_OWED}), graded(OWED_KEYS)], max_iterations=2
    )
    port, repo = first.port, first.repo

    def die(execution: int) -> None:
        if execution == 2:
            raise ConnectionResetError("the lane was killed between iterations")

    first.executor.on_execution = die
    with pytest.raises(ConnectionResetError):
        await first.run()

    assert completed(port) == {DIRECT_OWED}
    assert repo.pushed == repo.head
    commits_at_kill = len(repo.shas)
    # Where the fire died, asserted rather than described: the second
    # iteration's session had opened and had committed nothing yet.  Move the
    # kill inside iteration 2 instead and its own commit is on the repository,
    # so this pair is what keeps the case "between iterations".
    assert (commits_at_kill, len(first.executor.execution_prompts)) == (1, 2)
    evidence_at_kill = port.issues[DIRECT_OWED].body
    assert parse_criterion_evidence(evidence_at_kill).test == evaluation_observation(
        session_id=NATIVE_SESSION, iteration=1
    )
    moves, edits = written(port)
    del first

    second = Lane(
        evaluations=[graded(set(REMAINING), keys=REMAINING)],
        port=port,
        repo=repo,
        work_base_ref=BRANCH,
        resumed_head_sha=repo.pushed,
    )
    dispatched = (await second.arguments())["acceptance_criteria"]
    assert {criterion.id for criterion in dispatched} == set(REMAINING)
    assert len(dispatched) == len(REMAINING)

    events = await second.run()

    assert len(second.executor.execution_prompts) == 1
    assert len(second.executor.evaluation_prompts) == 1
    for prompt in (
        *second.executor.execution_prompts,
        *second.executor.evaluation_prompts,
    ):
        assert check_of(DIRECT_OWED) not in prompt
        assert all(check_of(key) in prompt for key in REMAINING)
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert {r.criterion_id for r in iterations[0].evaluation.criteria_results} == set(
        REMAINING
    )
    assert completed(port) == set(OWED_KEYS)
    assert closure(port).is_closed(SUBJECT)
    # Not re-graded and not re-stamped: the row run one wrote is the row
    # standing, and nothing addressed to that criterion was written after.
    assert port.issues[DIRECT_OWED].body == evidence_at_kill
    assert DIRECT_OWED not in {key for key, *_ in port.workflow_writes[moves:]}
    assert DIRECT_OWED not in {key for key, *_ in port.issue_writes[edits:]}
    assert len(repo.shas) == commits_at_kill + 1
    assert (await second.record()).branch == BRANCH


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


def _spying_on_cross_offs(lane, record) -> None:
    """Install one observer over the lane writer's cross-off act.

    *record* is handed the whole verdict once per act and the act then runs
    for real, so what each projection below reads is what the writer received
    rather than what a replaced writer was scripted with.
    """
    writer = lane.loop._lane_state
    written = writer.write_cross_offs

    async def observed(*, lane, dispatched, cross_offs):
        record(cross_offs)
        await written(lane=lane, dispatched=dispatched, cross_offs=cross_offs)

    writer.write_cross_offs = observed


def recording(lane) -> list[tuple[CrossOffState, ...]]:
    """Every whole verdict handed to the lane's writer, as its states.

    Per act: one tuple appended per call, because a state dropped from one
    verdict and a whole verdict never written are not the same defect.
    """
    states: list[tuple[CrossOffState, ...]] = []
    _spying_on_cross_offs(
        lane,
        lambda cross_offs: states.append(
            tuple(cross_off.state for cross_off in cross_offs)
        ),
    )
    return states


def dispatches(lane) -> list[tuple[str, ...]]:
    """Every roster handed to the lane's writer, as the criterion ids in it.

    Which criteria one iteration writes for is what a carry turns on, and
    the board's own registers cannot answer it: a second tick with identical
    facts edits no description and moves no state, so nothing there grows
    however many criteria the write was handed.
    """
    handed: list[tuple[str, ...]] = []
    writer = lane.loop._lane_state
    written = writer.write_cross_offs

    async def observed(*, lane, dispatched, cross_offs):
        handed.append(tuple(str(criterion.id) for criterion in dispatched))
        await written(lane=lane, dispatched=dispatched, cross_offs=cross_offs)

    writer.write_cross_offs = observed
    return handed


def asked_about(lane, key: str) -> list[str]:
    """Every write of *key* the board is ASKED for, the refused ones included.

    The refused ones are the point. A second tick with identical facts is
    answered UNCHANGED by the description surface and returns early at the
    state write, so the registers of what the board CHANGED cannot tell one
    tick from two. What the loop ASKED for can.
    """
    asked: list[str] = []
    port = lane.port
    edit, move = port.edit_description, port.set_workflow_state

    async def observed_edit(*, target, **rest):
        if target == key:
            asked.append(edit.__name__)
        return await edit(target=target, **rest)

    async def observed_move(*, issue_key, **rest):
        if issue_key == key:
            asked.append(move.__name__)
        return await move(issue_key=issue_key, **rest)

    port.edit_description = observed_edit
    port.set_workflow_state = observed_move
    return asked


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
    reason in place of a verdict, the iteration is rejected, the writer is
    handed the fourth state for each criterion, and the reading that failed
    is on the lane's run-event stream, keyed to each criterion's sub-issue
    at the sha the verdict would have been stamped with.
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
    } == {UNDEMONSTRATED_REASONS[UndemonstratedReason.workspace_not_the_graded_sha]}
    assert states == [tuple(CrossOffState.undemonstrated for _ in OWED_KEYS)]
    assert {
        key: (issue.state_name, issue.body) for key, issue in lane.port.issues.items()
    } == before
    assert lane.port.workflow_writes == []
    assert lane.port.issue_writes == []
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        (event.subject_key, event.graded_sha)
        for event in posted
        if event.kind is RunEventKind.CRITERION_GRADING_UNVERIFIED
    ] == [(key, lane.repo.head) for key in OWED_KEYS]
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
    """What a person reading the run's log finds beside what the record holds.

    The run's record is the lane's stream; this line is the same reading in
    the log, once per attempt rather than once per fan-in round, naming the
    sha the verdict would have been stamped with and which reading failed
    for each criterion it failed for.
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
    assert [entry["reasons"] for entry in undemonstrated] == [
        sorted(
            (key, UndemonstratedReason.workspace_not_the_graded_sha.value)
            for key in OWED_KEYS
        )
    ]


async def test_an_undemonstrated_criterion_has_no_evidence_row_written():
    """No verdict reaches the sub-issue, so nothing stamps its Evidence row.

    Asked of the row itself rather than of a write count: a row absent after
    the attempt is a row the one function that applies Evidence was never
    called for.
    """
    lane = Lane(evaluations=[native_evaluation()])
    lane.executor.on_evaluation = lane.leave_changes_behind

    await lane.run()

    for key in OWED_KEYS:
        with pytest.raises(ValueError, match="Evidence"):
            parse_criterion_evidence(lane.port.issues[key].body)


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


# ---------------------------------------------------------------------------
# KOD-610 — a criterion whose own check already passes at the lane's resolved
# base is no reading of the branch.
# ---------------------------------------------------------------------------


def base_reading(lane, *, satisfied, keys=OWED_KEYS) -> None:
    """Script one base reading for this lane, satisfying exactly *satisfied*."""
    lane.executor.base_readings = [base_echo(keys=keys, satisfied=satisfied)]


def workspace_moments(lane) -> list[tuple[str, int]]:
    """How many git calls this lane had made at each acquire and each release.

    The double hands the same path back for every acquisition, so nothing in
    the path distinguishes a read of one owned tree from a read of the next.
    The moment does: a read between the acquire and the release of the tree at
    the base is a read of THAT tree.
    """
    provider = lane.loop._workspace
    acquired, released = provider.acquire, provider.release
    moments: list[tuple[str, int]] = []

    async def observed_acquire(**arguments):
        path = await acquired(**arguments)
        moments.append(("acquire", len(lane.git.calls)))
        return path

    async def observed_release(workspace_path: str) -> None:
        moments.append(("release", len(lane.git.calls)))
        await released(workspace_path)

    provider.acquire = observed_acquire
    provider.release = observed_release
    return moments


def refuse_base_tree(lane, *, after: int = 0) -> None:
    """Refuse a tree at this lane's base, once *after* of them were cut."""
    provider = lane.loop._workspace
    acquired = provider.acquire
    cut = 0

    async def observed(**arguments):
        nonlocal cut
        if arguments.get("ref") == TRUNK_SHA:
            cut += 1
            if cut > after:
                raise WorkspaceError("no tree can be cut at the lane's base")
        return await acquired(**arguments)

    provider.acquire = observed


def move_the_base_tree(lane) -> None:
    """Let the tree cut at the base stand at some other commit."""
    provider = lane.loop._workspace
    acquired = provider.acquire

    async def observed(**arguments):
        path = await acquired(**arguments)
        if arguments.get("ref") == TRUNK_SHA:
            lane.git.heads[path] = "0" * 40
        return path

    provider.acquire = observed


def unreadable_base(lane) -> None:
    """Let this lane's base ref stop resolving to a commit."""
    source = lane.loop._source
    resolve = source.resolve_commit

    async def observed(*, cwd, ref):
        if ref in TRUNK_BRANCHES:
            raise GitSourceReadError(
                ref=ref, path=None, reason="the base ref names no commit"
            )
        return await resolve(cwd=cwd, ref=ref)

    source.resolve_commit = observed


async def test_a_check_the_base_already_passes_is_no_reading_of_the_branch():
    """A head pass whose check already passed at the base is not the branch's.

    Every criterion passes at the head and the base reading finds the first of
    them already passing there, where none of the work exists. That pass is
    therefore a reading of the base: the writer is handed the fourth state for
    it, its sub-issue is left byte-identical to what the run found, and the
    board holds no move and no edit for it. The other two are crossed off in
    the same tuple, so this is a distinction and not a refusal to write.

    What the board does get is the reason, once, on the lane's own stream:
    one ``criterion_satisfied_at_base`` event keyed to that sub-issue at the
    sha the verdict would have been stamped with, and no other comment.

    The two it crossed off record their gradings there too, one
    ``criterion_passed`` entry each (KOD-506), and the withheld one gets
    none: its Evidence row was not written, so no entry may claim it was.
    """
    first, *rest = OWED_KEYS
    lane = Lane(evaluations=[graded(OWED_KEYS)])
    base_reading(lane, satisfied={first})
    before = {
        key: (issue.state_name, issue.body) for key, issue in lane.port.issues.items()
    }
    states = recording(lane)

    await lane.run()

    assert states == [
        (CrossOffState.undemonstrated, CrossOffState.passed, CrossOffState.passed)
    ]
    assert (lane.port.issues[first].state_name, lane.port.issues[first].body) == before[
        first
    ]
    assert completed(lane.port) == set(rest)
    assert first not in {key for key, _ in lane.port.workflow_writes}
    assert first not in {key for key, _, _ in lane.port.issue_writes}
    # The one thing on the board that says why is the reason's own event on
    # the lane's stream: the lane's record, its first push, that event and
    # the gradings and crossings-off of the two it crossed off are every
    # comment this run wrote.
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [event.kind for event in posted] == [
        RunEventKind.FIRST_PUSH,
        RunEventKind.CRITERION_SATISFIED_AT_BASE,
        *(
            kind
            for _ in rest
            for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
        ),
    ]
    assert survived(posted, RunEventKind.CRITERION_SATISFIED_AT_BASE) == [
        (first, lane.repo.head)
    ]
    assert survived(posted, RunEventKind.CRITERION_PASSED) == [
        (key, lane.repo.head) for key in rest
    ]
    assert survived(posted, RunEventKind.ISSUE_CROSSED_OFF) == [
        (key, lane.repo.head) for key in rest
    ]
    assert len(posted) + len(lane.record_comments()) == len(lane.port.comments)
    assert {comment.issue_key for comment in lane.port.comments} == {SUBJECT}


async def test_the_lanes_log_names_the_criterion_the_base_already_satisfied():
    """What a person reading the run's log finds, once, per criterion.

    Keyed to the criterion because the fact is the criterion's, and carrying
    both shas and the command so the reading can be repeated: the commit the
    verdict would have been stamped with, the commit the base resolved to, and
    what was run there. The criterion the base does not satisfy gets no row.
    """
    first, *rest = OWED_KEYS
    lane = Lane(evaluations=[graded(OWED_KEYS)])
    base_reading(lane, satisfied={first})

    with structlog.testing.capture_logs() as logs:
        await lane.run()

    rows = [
        entry for entry in logs if entry.get("event") == "criterion_satisfied_at_base"
    ]
    assert [
        (entry["criterion"], entry["graded_sha"], entry["base_sha"], entry["command"])
        for entry in rows
    ] == [(first, lane.repo.head, TRUNK_SHA, BASE_COMMAND)]
    assert {entry["criterion"] for entry in rows}.isdisjoint(rest)


async def test_the_base_checks_run_in_a_second_owned_tree_at_the_resolved_base_sha():
    """The checks are run in a tree this loop owns, at the base's own commit.

    The base is the ref the lane recorded, resolved to a commit: a tree
    acquired at a name would follow the name. It is cut off the clone rather
    than off the grading tree, and it is asked what the grading tree is asked —
    its head and whether it holds changes — before the session and again after
    it, so a tree that moved under the session answers for nothing.

    Only the criteria this attempt passed are sent: a fail claims nothing about
    the branch, so no base reading could make it less proven. Each is sent with
    its own Check under its own id, because what is run at the base is that
    criterion's named check: an id with no check, or with another criterion's,
    names nothing the session could run.
    """
    first, second, third = OWED_KEYS
    lane = Lane(evaluations=[graded({first, second})])
    base_reading(lane, satisfied=set(), keys=(first, second))
    provider = lane.loop._workspace
    moments = workspace_moments(lane)

    await lane.run()

    assert len(provider.acquisitions) == 3
    assert provider.acquisitions[-1] == {
        "repo_path": CACHE_PATH,
        "repo_url": None,
        "ref": TRUNK_SHA,
        "branch_name": None,
        "create_branch": False,
        "cache_key": CACHE,
    }
    base_tree = lane.executor.base_workspaces[0]
    assert base_tree == lane.graded_in()
    assert base_tree != CACHE_PATH
    assert dispatched_ids(lane.executor.base_prompts[0]) == [first, second]
    assert dispatched_checks(lane.executor.base_prompts[0]) == {
        first: check_of(first),
        second: check_of(second),
    }
    assert check_of(third) not in lane.executor.base_prompts[0]
    # Both facts, twice: once before the session opened and once after it ended.
    assert [moment for moment, _ in moments[-2:]] == ["acquire", "release"]
    held = lane.git.calls[moments[-2][1] : moments[-1][1]]
    assert held.count(("current_sha", base_tree)) == 2
    assert held.count(("has_changes", base_tree)) == 2


async def test_the_base_tree_is_owned_only_after_the_graded_tree_is_released():
    """The lane owns one tree at a time, and gives every one of them back."""
    lane = Lane(evaluations=[graded(OWED_KEYS)])
    base_reading(lane, satisfied=set())
    provider = lane.loop._workspace

    await lane.run()

    trees = [call for call in provider.calls if call[0] in {"acquire", "release"}]
    assert [call[0] for call in trees[-4:]] == [
        "acquire",
        "release",
        "acquire",
        "release",
    ]
    assert [call[2] for call in trees[-4:] if call[0] == "acquire"] == [
        lane.repo.head,
        TRUNK_SHA,
    ]
    acquired = [call for call in provider.calls if call[0] == "acquire"]
    assert [call[0] for call in provider.calls].count("release") == len(acquired)


async def test_a_failing_criterion_is_never_read_at_the_base():
    """An attempt that passed nothing opens no base session at all.

    A fail is already unproven, so a base reading could change nothing about
    it, and a session that answers nothing is a session not opened.
    """
    lane = Lane(evaluations=[graded(set())])
    states = recording(lane)

    await lane.run()

    assert lane.executor.base_prompts == []
    assert states == [tuple(CrossOffState.failed for _ in OWED_KEYS)]


async def test_a_grading_that_did_not_stand_reads_no_base():
    """A verdict from a tree the sha does not name qualifies nothing.

    There is no pass to tell apart from a base pass, so no tree is cut at the
    base and the one read of the grading tree's dirtiness is still the only one.
    """
    lane = Lane(evaluations=[native_evaluation()])
    lane.executor.on_evaluation = lane.leave_changes_behind
    states = recording(lane)

    await lane.run()

    assert lane.executor.base_prompts == []
    assert states == [tuple(CrossOffState.undemonstrated for _ in OWED_KEYS)]
    graded_in = lane.executor.evaluation_workspaces[0]
    assert (
        len([call for call in lane.git.calls if call == ("has_changes", graded_in)])
        == 1
    )


async def test_the_base_checks_run_once_whatever_the_fan_in_costs():
    """A re-dispatched grading is still one reading of the base.

    The base tree does not move between fan-in attempts and the passing set is
    only known once a grade stands, so the reading is taken after the grade and
    not inside the dispatch: an iteration whose first echo answered the wrong
    roster pays for one base session, not one per attempt.
    """
    lane = Lane(
        evaluations=[graded(OWED_KEYS, keys=OWED_KEYS[:2]), graded(OWED_KEYS)],
        fan_in_max_attempts=2,
    )
    base_reading(lane, satisfied=set())
    states = recording(lane)

    await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert len(lane.executor.base_prompts) == 1
    assert states == [tuple(CrossOffState.passed for _ in OWED_KEYS)]


#: Every way the checks at the base do not get run, and the fixed reason each
#: one is recorded under. The reason is the harness's own text: no session
#: composed it, and nothing the session said reaches this row.
UNAVAILABLE_BASE = {
    "unresolvable-base": "the lane's base ref cannot be read",
    "tree-refused": "a tree at the base was refused",
    "tree-not-at-base": "the tree is not the base commit",
    "no-structured-output": "the checks at the base returned no usable answer",
    "invalid-shape": "the checks at the base returned no usable answer",
    "rate-limited": "the checks at the base returned no usable answer",
}


def arm_unavailable(lane, how: str) -> None:
    """Break the base reading the way *how* names, and nothing else."""
    if how == "unresolvable-base":
        lane.executor.on_evaluation = lambda _: unreadable_base(lane)
    elif how == "tree-refused":
        refuse_base_tree(lane)
    elif how == "tree-not-at-base":
        move_the_base_tree(lane)
    elif how == "no-structured-output":
        lane.executor.base_readings = [None]
    elif how == "rate-limited":
        lane.executor.base_readings = [RATE_LIMITED_BASE_READING]
    else:
        lane.executor.base_readings = [{"notTheAgreedContract": []}]


@pytest.mark.parametrize("how", sorted(UNAVAILABLE_BASE))
async def test_a_base_reading_that_cannot_be_taken_claims_no_pass(how):
    """No reading at the base is no pass of the branch, and no raise either.

    The run returns normally and the evaluator's own verdict still reaches the
    wire — the session did read the changeset — but every criterion it passed
    is handed to the writer as having no reading of the branch, because what
    was not read is exactly the branch's own contribution. No sub-issue is
    touched, and one row names the base ref, the fixed reason and the
    criteria the reading was going to take. The lane's stream names the base
    reading as unsettled for each of them, never as satisfied at the base.
    """
    lane = Lane(evaluations=[native_evaluation()])
    states = recording(lane)
    arm_unavailable(lane, how)

    with structlog.testing.capture_logs() as logs:
        events = await lane.run()

    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert [event.verdict for event in iterations] == [AcceptVerdict.accepted]
    assert states == [tuple(CrossOffState.undemonstrated for _ in OWED_KEYS)]
    assert lane.port.workflow_writes == []
    assert lane.port.issue_writes == []
    rows = [entry for entry in logs if entry.get("event") == "base_reading_unavailable"]
    assert [
        (entry["base_ref"], entry["reason"], tuple(entry["criterion_ids"]))
        for entry in rows
    ] == [("main", UNAVAILABLE_BASE[how], OWED_KEYS)]
    # Each criterion names the base reading as unsettled — nothing was read at
    # the base for it — and none is reported as satisfied there.
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert sorted(survived(posted, RunEventKind.CRITERION_BASE_READING_UNSETTLED)) == [
        (key, lane.repo.head) for key in sorted(OWED_KEYS)
    ]
    assert survived(posted, RunEventKind.CRITERION_SATISFIED_AT_BASE) == []


async def test_a_regression_is_still_taken_back_when_no_base_reading_could_be_taken():
    """A criterion this fire finished and has now broken is taken back anyway.

    A fail stands on the graded tree alone. Iteration 1 finishes two criteria
    with a reading at the base; iteration 2 breaks one of them and can cut no
    tree at the base at all — and the break is still recorded, once, because a
    missing base reading cannot turn a break into a non-break.
    """
    broken, kept, owed = OWED_KEYS
    lane = Lane(
        evaluations=[graded({broken, kept}), graded({kept})],
        max_iterations=2,
    )
    lane.executor.base_readings = [
        base_echo(keys=(broken, kept), satisfied=set()),
    ]
    refuse_base_tree(lane, after=1)

    await lane.run()

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
    assert lane.port.issues[owed].state_kind is WorkflowStateKind.UNSTARTED


async def test_no_base_tree_survives_the_iteration_that_opened_it():
    """Every tree an iteration cut is given back inside that iteration.

    Two iterations, each grading and each reading the base, and the provider's
    releases answer its acquisitions one for one. Nothing about a tree is
    carried on the loop's own state either: the state a killed run would have
    to re-adopt names no workspace, which is what keeps re-entry a read of the
    tracker rather than of a path that no longer exists.
    """
    _, kept, owed = OWED_KEYS
    lane = Lane(
        evaluations=[graded({kept}), graded({kept, owed})],
        max_iterations=2,
    )
    lane.executor.base_readings = [
        base_echo(keys=(kept,), satisfied=set()),
        base_echo(keys=(kept, owed), satisfied=set()),
    ]
    provider = lane.loop._workspace

    await lane.run()

    assert len(lane.executor.base_prompts) == 2
    acquired = [call[1] for call in provider.calls if call[0] == "acquire"]
    released = [call[1] for call in provider.calls if call[0] == "release"]
    assert len(acquired) == len(released) == 6
    assert set(RalphLoopState.__annotations__) == {
        "iteration",
        "verdict",
        "pending_failures",
        "iteration_records",
        "outcome",
        "iteration_commit_sha",
        "amendment_reports",
        "amendment_blocked",
        # The standing gradings the lapse reading carries between iterations:
        # cross-offs and their shas, naming no workspace and no path.
        "standing",
    }


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


async def test_the_evaluator_step_writes_one_state_move_and_one_evidence_row_per_key():
    """Two writes per graded criterion, both its own, both inside the step.

    The fixtures above read the board the cross-offs left; this one reads the
    port's own write journals, which say what the writer did rather than what
    the board looks like afterwards: one state move and one description-only
    edit per addressed sub-issue, in the roster's order, on the keys the
    verdict answered and on no others.  The two halves are the same
    sub-issue's because the Evidence row read back off each key carries THIS
    attempt's graded sha and the session and iteration it was graded in, and
    the row is compared as a whole value, so a third datum on it fails here
    (KOD-712 owns the one site both halves are written from).

    The cadence is the two instants around them: while the evaluation session
    is still running the board has not been written at all, and when the
    iteration event goes out both journals are complete and never move again.

    What this does not see, and what review has to read from the code: which
    step made the calls, covered by the caller guard above; a write by a step
    after the loop, covered by the post-loop fixture; the pointer at a second
    iteration, covered by the between-iterations fixture; and a second
    identical stamp of the same row, which the description surface absorbs as
    unchanged so no journal records it.  A ref double answering every ref
    with this lane's head is why the graded sha is shown to be the lane's own
    head and not the trunk base.
    """
    lane = Lane(evaluations=[native_evaluation()])
    at_session: list[tuple[int, int]] = []
    lane.executor.on_evaluation = lambda _: at_session.append(written(lane.port))
    at_event: list[tuple[list, list]] = []

    async for event in lane.loop.run(**await lane.arguments()):
        if isinstance(event, WorkflowIterationEvent):
            at_event.append(
                (
                    list(lane.port.workflow_writes),
                    [(key, title) for key, title, _ in lane.port.issue_writes],
                )
            )

    assert at_session == [(0, 0)]
    assert len(at_event) == 1
    moves, edits = at_event[-1]
    assert moves == [(key, LifecycleStage.DONE) for key in OWED_KEYS]
    assert edits == [(key, None) for key in OWED_KEYS]
    graded_sha = await LaneSource(lane.repo).resolve_commit(cwd="/w", ref=BRANCH)
    assert graded_sha == lane.repo.head != TRUNK_SHA
    assert {
        key: parse_criterion_evidence(lane.port.issues[key].body) for key in OWED_KEYS
    } == {
        key: CriterionEvidence(
            graded_sha=graded_sha,
            test=evaluation_observation(session_id=NATIVE_SESSION, iteration=1),
        )
        for key in OWED_KEYS
    }
    assert completed(lane.port) == set(OWED_KEYS)
    assert (
        list(lane.port.workflow_writes),
        [(key, title) for key, title, _ in lane.port.issue_writes],
    ) == at_event[-1]


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
    """Five iterations move five criteria and post only the lane's own kinds.

    The lane's comments are the record it rewrites in place and its own
    stream: the first push, and for each grading that passed both its
    grading entry (KOD-506) and the lane's crossing-off. Iteration n passes
    n criteria at its own head, so the five iterations announce one, two,
    three, four and five of them. Every iteration here crosses off the whole
    set it has passed so far and restamps each of their Evidence rows at its
    own head, so each of those restamps is its own entry — which is the point
    of the entry: the row a later head restamped and an entry naming only the
    first head would read as a row pointing behind its last grading. Each
    crossing-off is a distinct grading, keyed to its criterion and its head.

    A per-criterion state move is still not an event, so the three criteria
    nothing finished add nothing, the record is never duplicated, and no
    criterion sub-issue is commented on at all — every comment on this board
    is the lane issue's.
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
    # criterion the five iterations passed, and one comment each for the
    # record, the first push, the fifteen gradings the five iterations
    # recorded over them and the fifteen crossings-off that followed them.
    assert len(port.workflow_writes) == 5
    assert len(port.comments) == 32
    assert {comment.issue_key for comment in port.comments} == {SUBJECT}
    posted = await port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [(event.kind, event.subject_key) for event in posted] == [
        (RunEventKind.FIRST_PUSH, None),
        *(
            (kind, key)
            for index in range(5)
            for key in WIDE_CRITERIA[: index + 1]
            for kind in (RunEventKind.CRITERION_PASSED, RunEventKind.ISSUE_CROSSED_OFF)
        ),
    ]
    crossings = [
        (event.subject_key, event.graded_sha)
        for event in posted
        if event.kind is RunEventKind.ISSUE_CROSSED_OFF
    ]
    assert len(crossings) == 15
    assert len(set(crossings)) == len(crossings)
    assert {key for key, _ in crossings} == set(WIDE_CRITERIA[:5])
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

    The stream also carries each passing grading (KOD-506): the two the first
    iteration crossed off, and the one the second iteration restamped at its
    own head for the criterion it kept.
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
    assert [
        event.subject_key
        for event in posted
        if event.kind is RunEventKind.CRITERION_PASSED
    ] == [broken, kept, kept]
    assert (
        lane.port.issues[SUBJECT].state_name,
        lane.port.issues[SUBJECT].body,
    ) == subject_before
    assert SUBJECT not in {key for key, _, _ in lane.port.issue_writes}
    # The record, the first push, the refutation, and one grading entry and
    # one crossing-off per criterion each iteration passed: two at the first
    # head, one at the second.
    assert len(lane.port.comments) == 9
    assert [
        event.subject_key
        for event in posted
        if event.kind is RunEventKind.ISSUE_CROSSED_OFF
    ].count(kept) == 2


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
    _, current = await lane.criteria.read_entry(issue_key=SUBJECT)
    assert (broken in {criterion.id for criterion in current.criteria}) is owed_again


# ---------------------------------------------------------------------------
# The loop re-derives only a grading that has stopped standing (KOD-695).
# ---------------------------------------------------------------------------

CARRIED = OWED_KEYS[0]
#: A prefix the lane's own commits never touch, and one the commit made AFTER
#: the first grading does.
#:
#: The interval a standing grading is read against is that grading's own sha
#: to the new head, so the commit that produced the graded sha is not in it
#: and the commit the next iteration makes is. The double names each commit's
#: file after the commit's own index, so the second commit's is this one.
UNTOUCHED_PREFIX = "docs/"
TOUCHED_PREFIX = "lane-1.py"


def declaring(prefix: str, *, rederivation_class: str = "expensive") -> dict:
    """The first grading's echo: one criterion passes and declares its cost.

    *rederivation_class* is what that grading says the loop would have to do
    to take it again. It decides everything that follows once the prefix
    moves: an expensive grading goes back to the session, an observed one
    cannot and lapses.
    """
    return criteria_echo(
        keys=OWED_KEYS,
        passed={CARRIED},
        declared={
            CARRIED: {
                "rederivationClass": rederivation_class,
                "exercisedPaths": [prefix],
            }
        },
    )


def evidence_of(lane: Lane, key: str):
    return parse_criterion_evidence(lane.port.issues[key].body)


async def test_a_grading_that_still_stands_is_neither_dispatched_again_nor_re_ticked():
    """Two iterations, one head move, and one criterion nobody grades twice.

    The first grading passes one criterion and declares it expensive over a
    prefix the lane's commits never touch. At the second iteration the head
    has moved, so the loop reads the changed paths of the commit record
    between the sha that grading was taken at and the new head: nothing that
    grading exercised moved, so the verdict still stands. The session is not
    asked about it, and the board is not written for it — its state and its
    Evidence row are the ones the first grading left, byte for byte.

    The whole roster still reaches the gate: the iteration event carries a
    passing row for the carried criterion with the harness's own reason, so
    the denominator does not move between iterations and acceptance is never
    over a shrinking set.
    """
    lane = Lane(
        evaluations=[
            declaring(UNTOUCHED_PREFIX),
            criteria_echo(keys=OWED_KEYS[1:], passed=()),
        ],
        max_iterations=2,
    )
    handed = dispatches(lane)
    asked = asked_about(lane, CARRIED)
    events = await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert check_of(CARRIED) in lane.executor.evaluation_prompts[0]
    assert check_of(CARRIED) not in lane.executor.evaluation_prompts[1]
    assert all(
        check_of(key) in lane.executor.evaluation_prompts[1] for key in OWED_KEYS[1:]
    )

    first_grading = evidence_of(lane, CARRIED)
    assert lane.port.issues[CARRIED].state_kind is WorkflowStateKind.COMPLETED
    assert first_grading.graded_sha == lane.repo.shas[0]
    # Asserted on what the loop ASKS the board for rather than on what the
    # board holds afterwards: a second tick with identical facts is answered
    # UNCHANGED by the description surface and returns early at the state
    # write, so the board looks the same either way. The carried criterion is
    # in the first iteration's roster and in no later one, and the whole run
    # asks for its Evidence row and its state once each.
    assert handed[0] == tuple(OWED_KEYS)
    assert [roster for roster in handed[1:] if CARRIED in roster] == []
    assert sorted(asked) == ["edit_description", "set_workflow_state"]

    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    rows = {
        result.criterion_id: result
        for result in iterations[1].evaluation.criteria_results
    }
    assert set(rows) == set(OWED_KEYS)
    assert rows[CARRIED].passed is True
    assert rows[CARRIED].reasoning == CARRIED_REASON
    assert rows[CARRIED].rederivation_class is RederivationClass.expensive
    assert rows[CARRIED].exercised_paths == (UNTOUCHED_PREFIX,)


#: Two criteria one grading finishes together, and so at one sha.
CARRIED_PAIR = OWED_KEYS[:2]


async def test_the_standing_digest_is_read_once_per_graded_sha_over_that_sha_to_head():
    """Two standing gradings, one sha behind them, and one read of the record.

    Both criteria pass at the first iteration and declare themselves
    expensive over a prefix the lane's commits never touch, so at the second
    iteration both are asked the same question about the same interval: what
    moved between the sha they were graded at and the new head. That is one
    read per distinct standing sha, not one per criterion — a second read of
    one interval is a second place for the same answer to differ.

    The interval is that sha to the head, and not the lane's own base to the
    head. The lane's base-to-head digest contains the very commit the grading
    was taken at, so reading it would call every path-bound grading's
    prefixes moved because of the commit that graded them, and no such
    grading would ever carry.
    """
    lane = Lane(
        evaluations=[
            criteria_echo(
                keys=OWED_KEYS,
                passed=set(CARRIED_PAIR),
                declared={
                    key: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [UNTOUCHED_PREFIX],
                    }
                    for key in CARRIED_PAIR
                },
            ),
            criteria_echo(keys=OWED_KEYS[2:], passed=()),
        ],
        max_iterations=2,
    )
    await lane.run()

    graded_sha, head_sha = lane.repo.shas[0], lane.repo.shas[1]
    reads = [call for call in lane.git.calls if call[0] == "diff_summary"]
    standing = [call for call in reads if call[2] == graded_sha]
    assert standing == [("diff_summary", CACHE_PATH, graded_sha, head_sha)]

    # Not vacuous: the pair carried, so the reading the one digest produced is
    # the reading the second iteration acted on.
    assert all(
        check_of(key) not in lane.executor.evaluation_prompts[1] for key in CARRIED_PAIR
    )
    assert all(
        lane.port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in CARRIED_PAIR
    )
    assert all(evidence_of(lane, key).graded_sha == graded_sha for key in CARRIED_PAIR)


async def test_an_expensive_grading_whose_paths_moved_is_dispatched_again():
    """The same lane, one prefix later: the grading stops standing and is re-asked.

    The declared prefix is one the lane's own commits touch, so the commit
    record between the graded sha and the new head reaches beneath it. The
    loop may re-derive an expensive grading, so the criterion goes back to the
    session rather than being taken back, and the fresh grading restamps it at
    the new head.
    """
    lane = Lane(
        evaluations=[
            declaring(TOUCHED_PREFIX),
            criteria_echo(
                keys=OWED_KEYS,
                passed={CARRIED},
                declared={
                    CARRIED: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [TOUCHED_PREFIX],
                    }
                },
            ),
        ],
        max_iterations=2,
    )
    events = await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert check_of(CARRIED) in lane.executor.evaluation_prompts[1]
    assert lane.port.issues[CARRIED].state_kind is WorkflowStateKind.COMPLETED
    assert evidence_of(lane, CARRIED).graded_sha == lane.repo.shas[1]

    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    rows = {
        result.criterion_id: result
        for result in iterations[1].evaluation.criteria_results
    }
    assert set(rows) == set(OWED_KEYS)
    assert rows[CARRIED].reasoning != CARRIED_REASON
    # The re-derivation declares the class it holds.
    assert rows[CARRIED].rederivation_class is RederivationClass.expensive


#: A criterion that is not the first in the roster, so a comparison that read
#: only the head of either sequence would never see it.
MOVED = OWED_KEYS[1]


async def test_an_expensive_grading_whose_paths_moved_re_derived_as_cheap_raises():
    """A standing grading re-derived under another class is refused.

    The first grading passes a criterion that is not first in the roster and
    declares it expensive over a prefix the lane's next commit touches. At the
    second iteration its paths have moved, so the loop sends it back to the
    session; that grading passes it again and declares nothing, which reads
    cheap. The criterion held the expensive class, so the loop refuses before
    the second iteration's verdict reaches the writer: the Evidence row is
    still the one taken at the first graded sha (KOD-694, KOD-890).
    """
    lane = Lane(
        evaluations=[
            criteria_echo(
                keys=OWED_KEYS,
                passed={MOVED},
                declared={
                    MOVED: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [TOUCHED_PREFIX],
                    }
                },
            ),
            criteria_echo(keys=OWED_KEYS, passed={MOVED}),
        ],
        max_iterations=2,
    )
    verdicts = recording(lane)

    with pytest.raises(
        StickyClassError,
        match=f"{MOVED} holds the expensive re-derivation class and cannot be "
        "declared cheap",
    ):
        await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert check_of(MOVED) in lane.executor.evaluation_prompts[1]
    assert len(verdicts) == 1
    assert evidence_of(lane, MOVED).graded_sha == lane.repo.shas[0]


async def test_a_prefixless_path_bound_declaration_holds_as_cheap_across_iterations():
    """A class is compared after it is read, not as the session wrote it.

    The first grading fails one criterion and declares it expensive over no
    prefix at all. That declaration earns no exemption, so it reads cheap;
    the second grading fails it again and declares nothing, which is cheap
    too. The class the criterion holds never changed, so the fire runs both
    iterations out rather than refusing a class change that never happened
    (KOD-694, KOD-890).
    """
    lane = Lane(
        evaluations=[
            criteria_echo(
                keys=OWED_KEYS,
                passed=(),
                declared={
                    CARRIED: {"rederivationClass": "expensive", "exercisedPaths": []}
                },
            ),
            criteria_echo(keys=OWED_KEYS, passed=()),
        ],
        max_iterations=2,
    )
    events = await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert check_of(CARRIED) in lane.executor.evaluation_prompts[1]
    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    # Not vacuous: as the session wrote them, the two answers name two
    # classes, so a comparison of the raw answers would have refused here.
    declared = [
        {
            result.criterion_id: result
            for result in iteration.evaluation.criteria_results
        }[CARRIED].rederivation_class
        for iteration in iterations
    ]
    assert declared == [RederivationClass.expensive, RederivationClass.cheap]


async def test_an_observed_grading_whose_paths_moved_is_taken_back_as_lapsed():
    """The same prefix, one class later: the loop takes the grading back instead.

    The declared class rests on an observation somebody performed, so the loop
    cannot re-derive it: when the commit record between its own sha and the new
    head reaches beneath a prefix it exercised, the verdict stops standing and
    there is nothing this loop can do to reach it again. The criterion is
    therefore neither asked again nor left finished — it goes back out of its
    finished state, owed to whoever can observe it.

    This is the loop-side join the writer's take-back hangs off (KOD-413,
    KOD-698): the lapsed reading the standing partition produces has to reach
    the write, or the sub-issue sits finished at a sha whose tree has moved on
    and nothing on the board says so. So every hop is read here from the board
    and from the iteration the gate saw:

    - the session is not asked about it, because a lapse is not re-derivable;
    - its sub-issue is back out of the finished state;
    - its Evidence row keeps the sha it WAS graded at — not the new head — and
      carries the pointer that says the grading it names has lapsed, which is
      the only pair that shows the gap between what was graded and where the
      branch went;
    - a new fire over the subject, reading it the way its entry barrier does,
      owes this criterion again, while the two criteria this iteration did
      finish stay finished and are not owed, so the openness is this one
      criterion's and not the whole roster going back;
    - the roster the gate read carries it as not passing, with the harness's
      own lapse reason rather than a session's prose, so the denominator never
      shrinks to the two the session was handed.
    """
    lane = Lane(
        evaluations=[
            declaring(TOUCHED_PREFIX, rederivation_class="observed"),
            criteria_echo(keys=OWED_KEYS[1:], passed=set(OWED_KEYS[1:])),
        ],
        max_iterations=2,
    )
    events = await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert check_of(CARRIED) in lane.executor.evaluation_prompts[0]
    assert check_of(CARRIED) not in lane.executor.evaluation_prompts[1]

    assert lane.port.issues[CARRIED].state_kind is WorkflowStateKind.UNSTARTED
    lapsed = evidence_of(lane, CARRIED)
    assert lapsed.graded_sha == lane.repo.shas[0]
    assert lane.repo.shas[0] != lane.repo.head
    assert LAPSE_POINTER in lapsed.test

    _, current = await lane.criteria.read_entry(issue_key=SUBJECT)
    assert {criterion.id for criterion in current.criteria} == {CARRIED}
    subject = await lane.port.read_issue(issue_key=SUBJECT)
    assert subject.state_kind is not WorkflowStateKind.COMPLETED
    assert all(
        lane.port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in OWED_KEYS[1:]
    )

    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    rows = {
        result.criterion_id: result
        for result in iterations[1].evaluation.criteria_results
    }
    assert set(rows) == set(OWED_KEYS)
    assert (rows[CARRIED].passed, rows[CARRIED].reasoning) == (False, LAPSE_REASON)
    assert rows[CARRIED].rederivation_class is RederivationClass.observed
    assert rows[CARRIED].exercised_paths == (TOUCHED_PREFIX,)


@pytest.mark.parametrize("second_verdict", ["passed", "failed"])
async def test_a_cheap_class_re_derived_as_expensive_raises(second_verdict):
    """A criterion that held cheap may not be re-derived as expensive.

    The first grading fails one criterion and declares nothing, so it holds
    the cheap class; a failed grading is re-derived, and the second grading
    declares it expensive over a prefix. Whether that second grading passes
    or fails the criterion, the class changed, and the loop refuses before
    the second iteration's verdict reaches the writer (KOD-694, KOD-890).
    """
    lane = Lane(
        evaluations=[
            criteria_echo(keys=OWED_KEYS, passed=()),
            criteria_echo(
                keys=OWED_KEYS,
                passed={CARRIED} if second_verdict == "passed" else (),
                declared={
                    CARRIED: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [TOUCHED_PREFIX],
                    }
                },
            ),
        ],
        max_iterations=2,
    )
    unwritten = lane.port.issues[CARRIED].body
    verdicts = recording(lane)

    with pytest.raises(
        StickyClassError,
        match="holds the cheap re-derivation class and cannot be declared expensive",
    ):
        await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    # Only the first iteration's verdict was handed to the writer.
    assert verdicts == [tuple(CrossOffState.failed for _ in OWED_KEYS)]
    assert lane.port.issues[CARRIED].body == unwritten
    assert lane.port.issues[CARRIED].state_kind is WorkflowStateKind.UNSTARTED


# ---------------------------------------------------------------------------
# A grading the loop cannot re-derive is asked about, once (KOD-699).
# ---------------------------------------------------------------------------

#: The criterion whose grading rests on a performed observation. It is the one
#: ``declaring`` passes, so the two arms differ in the class that grading
#: declares and in nothing else.
LAPSED = CARRIED


def escalation_writes(lane, key: str) -> list[tuple[str, WorkflowStateKind]]:
    """Every occurrence write ASKED for, with the criterion's state at that moment.

    The board cannot answer either question: an identical body is answered
    unchanged and a label already present returns early, so a second raise
    leaves the same board as one. And the order is a property — a question
    naming a criterion the board still shows as finished sends a reader to a
    satisfied row.
    """
    asked: list[tuple[str, WorkflowStateKind]] = []
    port = lane.port
    prefix = native_operation().marker_prefixes["escalation"]
    upsert = port.upsert_comment

    async def observed(*, target, marker, body, **rest):
        if marker.startswith(f"[{prefix}:"):
            asked.append((marker, port.issues[key].state_kind))
        return await upsert(target=target, marker=marker, body=body, **rest)

    port.upsert_comment = observed
    return asked


def occurrence(lane, key: str):
    """The occurrence body the board holds for *key*'s lapse question."""
    marker = compose_comment_marker(
        prefixes=native_operation().marker_prefixes,
        purpose="escalation",
        lane=SUBJECT,
        occurrence_key=f"{key}:lapse",
    )
    found = [
        comment for comment in lane.port.comments if comment.body.startswith(marker)
    ]
    assert len(found) == 1, found
    return found[0], LaneEscalation.model_validate_json(
        found[0].body.partition("\n")[2]
    )


async def test_an_observed_grading_asks_one_question_and_the_lane_keeps_grading():
    """Three iterations, one lapse, one question, and a lane that goes on grading.

    The first grading passes one criterion and declares it observed over a
    prefix the lane's own later commits touch. At the second iteration the
    commit record between that grading's sha and the new head reaches beneath
    that prefix, so the grading has stopped standing — and the loop cannot
    take it again, because the observation was performed rather than derived.
    The criterion goes back to unstarted carrying the sha it was graded at,
    and one question is raised on the lane's own issue, addressed under that
    criterion.

    Counting is on what the loop ASKS the port for, never on what the board
    holds: an identical occurrence body is answered unchanged and a label
    already present returns early, so a board-derived count cannot tell one
    raise from two. The third iteration is what makes the count mean
    something — the criterion is still lapsed there and no longer newly so, so
    the transition the raise is keyed on has passed and nothing is asked
    again.

    The lapse comes from the commit record and from nothing else: no reading
    is injected, and the record is read once, over that grading's own sha to
    the new head. The lane's other two criteria are graded at every iteration
    throughout.
    """
    lane = Lane(
        evaluations=[
            declaring(TOUCHED_PREFIX, rederivation_class="observed"),
            criteria_echo(keys=OWED_KEYS[1:], passed=()),
            criteria_echo(keys=OWED_KEYS[1:], passed=()),
        ],
        max_iterations=3,
    )
    raised = escalation_writes(lane, LAPSED)
    asked = asked_about(lane, LAPSED)
    events = await lane.run()

    graded_sha, head_sha = lane.repo.shas[0], lane.repo.shas[1]
    marker = compose_comment_marker(
        prefixes=native_operation().marker_prefixes,
        purpose="escalation",
        lane=SUBJECT,
        occurrence_key=f"{LAPSED}:lapse",
    )
    # One question for one transition, addressed under the criterion whose
    # grading lapsed, and written only once the board no longer shows that
    # criterion as finished.
    assert raised == [(marker, WorkflowStateKind.UNSTARTED)]
    comment, escalation = occurrence(lane, LAPSED)
    assert comment.issue_key == SUBJECT
    assert escalation.issue_id == SUBJECT
    assert escalation.escalation_key == f"{LAPSED}:lapse"
    assert escalation.raised_at_sha == graded_sha
    assert escalation.interim_basis == TOUCHED_PREFIX
    assert lane.port.classification_writes == [(SUBJECT, "decision")]
    # One judged window per raise, counted where the board cannot: the judge
    # opens its own session per question and the fake keeps every one.
    assert len(lane.executor.judge_sessions) == 1

    # The lapse is the commit record's own answer: nothing injects a reading,
    # and the record for the standing grading is read once, over that
    # grading's own sha to the head this iteration grades.
    standing = [
        call
        for call in lane.git.calls
        if call[0] == "diff_summary" and call[2] in lane.repo.shas
    ]
    assert standing == [("diff_summary", CACHE_PATH, graded_sha, head_sha)]

    # Not re-derived, and not returned to Done: the criterion leaves the
    # session's roster for good and the board says it is owed again.
    assert len(lane.executor.evaluation_prompts) == 3
    assert check_of(LAPSED) in lane.executor.evaluation_prompts[0]
    assert all(
        check_of(LAPSED) not in prompt
        for prompt in lane.executor.evaluation_prompts[1:]
    )
    assert lane.port.issues[LAPSED].state_kind is WorkflowStateKind.UNSTARTED
    assert evidence_of(lane, LAPSED).graded_sha == graded_sha
    # The tick, then the take-back's one re-stamp of that row: the state is
    # asked for once in the whole run, so nothing moved it twice and nothing
    # put it back.
    assert asked == ["edit_description", "set_workflow_state", "edit_description"]
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        event.kind
        for event in posted
        if event.kind is RunEventKind.CRITERION_REFUTED and event.subject_key == LAPSED
    ] == []

    # The rest of the lane keeps being graded, at every iteration after the
    # lapse as well as the one it happened at.
    for prompt in lane.executor.evaluation_prompts[1:]:
        assert all(check_of(key) in prompt for key in OWED_KEYS[1:])
    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 3
    rows = {
        result.criterion_id: result
        for result in iterations[2].evaluation.criteria_results
    }
    assert set(rows) == set(OWED_KEYS)
    assert rows[LAPSED].passed is False
    assert rows[LAPSED].reasoning == LAPSE_REASON


async def test_a_lapse_with_no_escalation_writer_refuses_before_the_session():
    """A lane that could not ask the question does not take the criterion back.

    The escalation role is resolved where the reading is composed, before the
    session this iteration would grade in — so a deployment that configured no
    raiser refuses while the board still reads as the pass the first grading
    left. The refusal is the loop's own typed one, the second session is never
    opened, and no occurrence is written.
    """
    lane = Lane(
        evaluations=[
            declaring(TOUCHED_PREFIX, rederivation_class="observed"),
            criteria_echo(keys=OWED_KEYS[1:], passed=()),
        ],
        max_iterations=2,
        raises_lapse_questions=False,
    )
    raised = escalation_writes(lane, LAPSED)

    with pytest.raises(NativeWriteRefusalError, match="lapse escalation writer"):
        await lane.run()

    assert len(lane.executor.evaluation_prompts) == 1
    assert raised == []
    assert lane.port.issues[LAPSED].state_kind is WorkflowStateKind.COMPLETED
    assert evidence_of(lane, LAPSED).graded_sha == lane.repo.shas[0]


# ---------------------------------------------------------------------------
# A lapse is a state move; a grading behind head that still stands is not
# (KOD-409).
# ---------------------------------------------------------------------------

#: The criterion whose grading still stands at the new head: graded at the same
#: sha as the lapsed one, over a prefix the lane's own commits never touch.
STANDING = OWED_KEYS[1]


async def test_a_lapse_is_a_state_move_back_while_a_standing_grading_stays_counted():
    """Two gradings at one sha, one head move, and one record read for both.

    Both criteria pass at the first iteration, at the same sha, behind the
    head the second iteration grades. They differ in two things only: the
    class each grading declares and the prefix it names. The one resting on a
    performed observation names a prefix the lane's own next commit touches;
    the expensive one names a prefix nothing in this lane ever touches.

    So the lapse IS a state move: that sub-issue goes back to the team's
    unstarted state, keeping the sha it was graded at with its pointer saying
    that grading lapsed, and the iteration row reports it ungraded with the
    harness's own lapse reason rather than refuted — nothing announces a
    regression for it. The other arm moves nowhere: it stays finished, keeps
    the sha it was graded at, is not asked about again, and its row carries
    the carried reason with the class and prefixes its own grading declared.

    Clause 5 of the Check is what the shared sha buys: both arms were graded
    at one commit, read against one head, out of one commit-record read. An
    implementation keyed on the graded sha equalling the head answers the same
    thing to both, so whichever answer it gives, one arm reds. This fixture
    states no lapse arithmetic of its own — it imports neither the rule nor
    its reading and injects no reading anywhere. Every reading in it comes
    from the loop's own call.

    The owning issue is written by nobody: its finished state is the tracker's
    own rollup over these sub-issues, which is also what a new fire over the
    same subject reads — it owes the lapsed criterion again and does not owe
    the standing one.
    """
    lane = Lane(
        evaluations=[
            criteria_echo(
                keys=OWED_KEYS,
                passed={LAPSED, STANDING},
                declared={
                    LAPSED: {
                        "rederivationClass": "observed",
                        "exercisedPaths": [TOUCHED_PREFIX],
                    },
                    STANDING: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [UNTOUCHED_PREFIX],
                    },
                },
            ),
            criteria_echo(keys=OWED_KEYS[2:], passed=()),
        ],
        max_iterations=2,
    )
    asked = asked_about(lane, STANDING)
    events = await lane.run()

    graded_sha, head_sha = lane.repo.shas[0], lane.repo.shas[1]
    assert graded_sha != head_sha
    reads = [
        call
        for call in lane.git.calls
        if call[0] == "diff_summary" and call[2] in lane.repo.shas
    ]
    assert reads == [("diff_summary", CACHE_PATH, graded_sha, head_sha)]

    # The lapse arm: a state move back, not a refutation.
    assert lane.port.issues[LAPSED].state_kind is WorkflowStateKind.UNSTARTED
    assert LAPSED not in completed(lane.port)
    lapsed_row = evidence_of(lane, LAPSED)
    assert lapsed_row.graded_sha == graded_sha
    assert lapsed_row.test.endswith(LAPSE_POINTER)
    # Done and then nothing: the stage write that finished it is the only one
    # the board was handed for it, so it never passed through In Review on the
    # way back — and that stage is written nowhere in the whole run.
    assert [stage for key, stage in lane.port.workflow_writes if key == LAPSED] == [
        LifecycleStage.DONE
    ]
    assert LifecycleStage.IN_REVIEW not in {
        stage for _, stage in lane.port.workflow_writes
    }
    # The owning issue's own state is written by nobody: it is the rollup.
    assert [key for key, _ in lane.port.workflow_writes if key == SUBJECT] == []
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        event.subject_key
        for event in posted
        if event.kind is RunEventKind.CRITERION_REFUTED
    ] == []

    # The standing arm: behind head, not lapsed, and still counted.
    assert lane.port.issues[STANDING].state_kind is WorkflowStateKind.COMPLETED
    assert STANDING in completed(lane.port)
    assert evidence_of(lane, STANDING).graded_sha == graded_sha
    assert check_of(STANDING) not in lane.executor.evaluation_prompts[1]
    assert sorted(asked) == ["edit_description", "set_workflow_state"]

    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    rows = {
        result.criterion_id: result
        for result in iterations[1].evaluation.criteria_results
    }
    assert set(rows) == set(OWED_KEYS)
    assert rows[LAPSED].passed is False
    assert rows[LAPSED].reasoning == LAPSE_REASON
    assert rows[STANDING].passed is True
    assert rows[STANDING].reasoning == CARRIED_REASON
    assert rows[STANDING].rederivation_class is RederivationClass.expensive
    assert rows[STANDING].exercised_paths == (UNTOUCHED_PREFIX,)

    # What a new fire over the same subject owes, read the way its entry
    # barrier reads it: the lapsed criterion is owed again, the standing one
    # is not.
    _, current = await lane.criteria.read_entry(issue_key=SUBJECT)
    owed = {criterion.id for criterion in current.criteria}
    assert LAPSED in owed
    assert STANDING not in owed


# ---------------------------------------------------------------------------
# An iteration whose whole roster is withheld refuses before the session
# (KOD-695).
# ---------------------------------------------------------------------------


async def test_a_lane_with_every_criterion_withheld_refuses_before_it_opens_a_session():
    """Three iterations, and a third with nothing left that may be graded.

    The first grading passes one criterion resting on a performed observation,
    over a prefix the lane's own next commit touches. The second passes the
    other two, expensive over a prefix nothing in this lane touches, and the
    first one lapses there — so the gate does not clear and a third iteration
    runs. By that third iteration the two expensive gradings still stand, and
    the lapsed one may not be re-derived, so the set the session would be
    asked about is empty.

    That is the one arrangement in which the loop's own guard fires: only a
    STANDING grading is withheld, so an iteration that ran at all should have
    something failing or owed. The loop says so before it opens a session
    rather than grading an empty roster and writing a partial verdict — the
    backend is asked twice in a three-iteration run, and the third iteration
    writes no cross-off at all. The echo list holds two answers, so a third
    session would also trip the double's own refusal to answer one.
    """
    lane = Lane(
        evaluations=[
            criteria_echo(
                keys=OWED_KEYS,
                passed={CARRIED},
                declared={
                    CARRIED: {
                        "rederivationClass": "observed",
                        "exercisedPaths": [TOUCHED_PREFIX],
                    },
                    **{
                        key: {
                            "rederivationClass": "expensive",
                            "exercisedPaths": [UNTOUCHED_PREFIX],
                        }
                        for key in OWED_KEYS[1:]
                    },
                },
            ),
            criteria_echo(
                keys=OWED_KEYS[1:],
                passed=set(OWED_KEYS[1:]),
                declared={
                    key: {
                        "rederivationClass": "expensive",
                        "exercisedPaths": [UNTOUCHED_PREFIX],
                    }
                    for key in OWED_KEYS[1:]
                },
            ),
        ],
        max_iterations=3,
    )
    handed = dispatches(lane)

    with pytest.raises(NativeWriteRefusalError, match="no criterion left to grade"):
        await lane.run()

    assert len(lane.executor.evaluation_prompts) == 2
    assert len(handed) == 2
    assert lane.executor.evaluations == []
    # Not vacuous: the third iteration is the one that refused, and the two
    # readings that withheld the whole roster are the loop's own — the pair
    # carried over the record for their own sha to the third head, and the
    # observed grading was taken back at the second.
    assert len(lane.repo.shas) == 3
    assert lane.port.issues[LAPSED].state_kind is WorkflowStateKind.UNSTARTED
    assert completed(lane.port) == set(OWED_KEYS[1:])
    assert all(
        evidence_of(lane, key).graded_sha == lane.repo.shas[1] for key in OWED_KEYS[1:]
    )


#: The fixture criteria of the mutation arm, and the check each one names.
#:
#: Real source in a real tree, so "this check still passed with the behaviour
#: it names gone" is read off the checks rather than scripted onto a double.
MUTATION_WIRED = f"{SUBJECT}/check-wired"
MUTATION_TAUTOLOGY = f"{SUBJECT}/check-tautology"
TOLD_APART = {MUTATION_WIRED: "wired", MUTATION_TAUTOLOGY: "tautology"}


def mutation_lane(root, checks=TOLD_APART, **rest) -> Lane:
    """One lane whose roster is *checks* and whose trees are really written."""
    return Lane(
        port=wide_board(tuple(checks)),
        evaluations=[],
        mutation=MutationLaneFixture(root=root, checks=checks),
        **rest,
    )


def finished(port, keys) -> set[str]:
    """Which of *keys* the board holds in its finished state."""
    return {
        key
        for key in keys
        if port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    }


def survived(events, kind=RunEventKind.CRITERION_CHECK_SURVIVED_MUTATION):
    """The sub-issue keys and shas the stream names for one event kind."""
    return [
        (event.subject_key, event.graded_sha) for event in events if event.kind is kind
    ]


async def test_two_fixture_criteria_are_told_apart_by_the_mutant_tree(tmp_path):
    """One run, two criteria, and only one of them says anything.

    Both checks pass in the graded tree. In a copy of it with the behaviour
    they name removed, the check that hands its double to the subject fails
    and the one that asserts about its own inputs does not — so the first
    keeps its pass and the second is withheld with the reading that failed.
    """
    lane = mutation_lane(tmp_path)

    events = await lane.run()

    assert finished(lane.port, TOLD_APART) == {MUTATION_WIRED}
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert survived(posted) == [(MUTATION_TAUTOLOGY, lane.repo.head)]
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert {
        result.criterion_id: (result.passed, result.reasoning)
        for result in iterations[-1].evaluation.criteria_results
    } == {
        MUTATION_WIRED: (True, "Ran the check wired names."),
        MUTATION_TAUTOLOGY: (
            False,
            UNDEMONSTRATED_REASONS[UndemonstratedReason.check_survived_mutation],
        ),
    }


async def test_a_criterion_whose_check_fails_in_the_mutant_tree_keeps_its_verdict(
    tmp_path,
):
    """A fail read in the mutant tree is no more the branch's than a pass is.

    So the harness withholds and never converts: the criterion is crossed off
    at the sha the clean grading read, and no reading-failure event on the
    lane names it. The entries keyed to it are its own passing grading at
    that sha, which every cross-off that finishes a criterion records
    (KOD-506), and the lane's crossing-off of it at the same sha.
    """
    lane = mutation_lane(tmp_path)

    await lane.run()

    assert (
        parse_criterion_evidence(lane.port.issues[MUTATION_WIRED].body).graded_sha
        == lane.repo.head
    )
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert [
        (event.kind, event.graded_sha)
        for event in posted
        if event.subject_key == MUTATION_WIRED
    ] == [
        (RunEventKind.CRITERION_PASSED, lane.repo.head),
        (RunEventKind.ISSUE_CROSSED_OFF, lane.repo.head),
    ]


async def test_the_surviving_criterion_reaches_no_passing_state(tmp_path):
    """The fourth state reaches the writer, and the iteration is rejected.

    Asked of the value the writer received rather than of the board alone: a
    criterion recorded as undemonstrated and one the writer was simply never
    told about leave the same unfinished sub-issue behind.
    """
    lane = mutation_lane(tmp_path)
    states = recording(lane)

    events = await lane.run()

    assert [sorted(verdict) for verdict in states] == [
        sorted((CrossOffState.passed, CrossOffState.undemonstrated))
    ]
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert [event.verdict for event in iterations] == [AcceptVerdict.rejected]


async def test_no_mutation_reading_runs_when_the_workspace_did_not_stand(tmp_path):
    """Nothing read in a copy of a tree nobody can name stands either.

    The mutation reading is taken in a copy of the graded tree, so a graded
    tree that was not the branch's makes that copy worth no more than the
    original: the whole roster carries the workspace reading and no removal
    session opens at all.
    """
    lane = mutation_lane(tmp_path)
    lane.executor.on_evaluation = lane.leave_changes_behind
    states = recording(lane)

    events = await lane.run()

    assert lane.executor.mutation_prompts == []
    assert states == [tuple(CrossOffState.undemonstrated for _ in TOLD_APART)]
    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert {
        result.reasoning for result in iterations[-1].evaluation.criteria_results
    } == {UNDEMONSTRATED_REASONS[UndemonstratedReason.workspace_not_the_graded_sha]}
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert survived(posted) == []
    assert sorted(survived(posted, RunEventKind.CRITERION_GRADING_UNVERIFIED)) == (
        sorted((key, lane.repo.head) for key in TOLD_APART)
    )


async def test_the_authored_arm_opens_no_mutation_session(tmp_path):
    """No sha to stand for, so no tree to take a second reading in.

    An authored fire grades the ref it asked for and claims nothing about a
    commit, so there is no tree for a second reading to be taken in and no
    cross-off for one to withhold.
    """
    authored = make_criteria("Tests pass")
    lane = mutation_lane(tmp_path)
    lane.executor.evaluate_in = lambda _: {
        "criteriaResults": [
            {
                "criterionId": criterion.id,
                "criterion": criterion.text,
                "passed": True,
                "reasoning": "Observed the selected check.",
            }
            for criterion in authored
        ]
    }
    arguments = {
        **await lane.arguments(),
        "tracker_spec": None,
        "acceptance_criteria": list(authored),
    }

    async for _ in lane.loop.run(**arguments):
        pass

    assert lane.executor.evaluation_prompts
    assert lane.executor.mutation_prompts == []
    assert finished(lane.port, TOLD_APART) == set()


async def test_a_removal_that_pushed_to_the_loop_branch_refuses_the_iteration(tmp_path):
    """The removing session holds write permission at the graded sha.

    The node's own branch-moved refusal runs before the mutation pass, so a
    push made by that session is caught after it or not at all — and an
    iteration graded against a branch that has since moved is refused rather
    than recorded.
    """
    lane = mutation_lane(tmp_path)
    fixture = lane.mutation
    removed = fixture.remove_in

    def removes_and_pushes(workspace):
        removed(workspace)
        lane.repo.commit()

    fixture.remove_in = removes_and_pushes
    lane.executor.on_mutation = removes_and_pushes

    with pytest.raises(NativeWriteRefusalError, match="during the mutation reading"):
        await lane.run()

    assert finished(lane.port, TOLD_APART) == set()


#: The fixture criterion whose check constructs a double and wires it nowhere.
MUTATION_UNWIRED = f"{SUBJECT}/check-unwired"
VACUOUS = {MUTATION_UNWIRED: "unwired"}


def reasons_given(lane) -> list[tuple[CrossOffState, UndemonstratedReason | None]]:
    """Every cross-off the lane's writer received, as state and reading.

    Flat across acts, where `recording` is per act: this projection is about
    which reading each cross-off carries and not about how the verdicts were
    grouped into writes.
    """
    received: list[tuple[CrossOffState, UndemonstratedReason | None]] = []
    _spying_on_cross_offs(
        lane,
        lambda cross_offs: received.extend(
            (cross_off.state, cross_off.undemonstrated_reason)
            for cross_off in cross_offs
        ),
    )
    return received


async def test_a_check_whose_double_reaches_no_subject_resolves_undemonstrated(
    tmp_path,
):
    """A check that observes nothing about the subject survives every removal.

    Its double is constructed and handed to none of the subjects under test,
    so every assertion in it is about the double: it passes in the graded tree
    and in a copy with the behaviour gone, for the same reason both times. No
    second reading is taken for it — the mutation reading already resolves it
    and names why — and its sub-issue is untouched.
    """
    lane = mutation_lane(tmp_path, checks=VACUOUS)
    before = (
        lane.port.issues[MUTATION_UNWIRED].state_name,
        lane.port.issues[MUTATION_UNWIRED].body,
    )
    received = reasons_given(lane)

    await lane.run()

    assert received == [
        (CrossOffState.undemonstrated, UndemonstratedReason.check_survived_mutation)
    ]
    assert finished(lane.port, VACUOUS) == set()
    assert (
        lane.port.issues[MUTATION_UNWIRED].state_name,
        lane.port.issues[MUTATION_UNWIRED].body,
    ) == before
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(lane.port.issues[MUTATION_UNWIRED].body)
    posted = await lane.port.lane_run_events(issue_key=SUBJECT, lane_key=SUBJECT)
    assert survived(posted) == [(MUTATION_UNWIRED, lane.repo.head)]


async def test_the_vacuous_reading_is_the_same_value_the_mutation_reading_produced(
    tmp_path,
):
    """ "That same resolution": one value, compared rather than restated.

    Read off the two runs in this module rather than from repeated literals,
    so a second mechanism for one state — a reason of its own, a kind of its
    own — would show up here as a difference between them.

    The kinds compared are those keyed to a criterion the run did not
    finish: the survivor run also crosses its other criterion off, and that
    records a passing grading (KOD-506) the vacuous run has no criterion to
    record, which is a difference in the rosters and not in the reading.
    """
    vacuous = mutation_lane(tmp_path / "vacuous", checks=VACUOUS)
    vacuous_received = reasons_given(vacuous)
    await vacuous.run()
    vacuous_posted = await vacuous.port.lane_run_events(
        issue_key=SUBJECT, lane_key=SUBJECT
    )

    survivor = mutation_lane(tmp_path / "survivor", checks=TOLD_APART)
    survivor_received = reasons_given(survivor)
    await survivor.run()
    survivor_posted = await survivor.port.lane_run_events(
        issue_key=SUBJECT, lane_key=SUBJECT
    )

    withheld = [
        (state, reason)
        for state, reason in survivor_received
        if state is CrossOffState.undemonstrated
    ]
    assert vacuous_received == withheld
    vacuous_done = finished(vacuous.port, VACUOUS)
    survivor_done = finished(survivor.port, TOLD_APART)
    vacuous_kinds = {
        e.kind
        for e in vacuous_posted
        if e.subject_key is not None and e.subject_key not in vacuous_done
    }
    assert vacuous_kinds, (
        "non-vacuity: the vacuous run keyed some reading to its sub-issue"
    )
    assert vacuous_kinds == {
        e.kind
        for e in survivor_posted
        if e.subject_key is not None and e.subject_key not in survivor_done
    }


async def test_a_vacuous_check_does_not_count_toward_the_iterations_passes(tmp_path):
    """It is the only criterion that would otherwise have passed, and it does not.

    So the iteration's pass count excludes it and the verdict is a rejection:
    a check that cannot fail counting toward the demonstrated set is exactly
    the accounting this reading exists to refuse.
    """
    lane = mutation_lane(tmp_path, checks=VACUOUS)

    events = await lane.run()

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert [event.verdict for event in iterations] == [AcceptVerdict.rejected]
    assert [
        (result.criterion_id, result.passed, result.reasoning)
        for result in iterations[-1].evaluation.criteria_results
    ] == [
        (
            MUTATION_UNWIRED,
            False,
            UNDEMONSTRATED_REASONS[UndemonstratedReason.check_survived_mutation],
        )
    ]
    assert iterations[-1].trajectory.best_passed_count == 0
    assert [record.passed_count for record in iterations[-1].trajectory.records] == [0]
    assert finished(lane.port, VACUOUS) == set()


def test_a_check_that_cannot_run_is_a_broken_fixture(tmp_path):
    """The instrument's own guarantee, asserted rather than only documented.

    Every reading above is "this check discriminates", read off a check really
    run in two copies of a tree. A runner that came back `False` for a check
    that raised would report a broken fixture as the check failing, and a
    criterion whose check no longer loads would then keep its pass under a
    reading that observed nothing. So an `AssertionError` is the check failing
    and nothing else is: anything else propagates.
    """
    write_fixture_tree(tmp_path)
    (tmp_path / "checks" / "broken.py").write_text(
        "def check():\n    raise TypeError('this check cannot run')\n"
    )

    with pytest.raises(TypeError, match="this check cannot run"):
        run_named_check(tmp_path, "broken")


#: Where the deliverable branch stands once the stall landing consolidated the
#: best iteration onto it: a sha of the merger's own, so no commit this lane
#: made can stand in for it.
LANDED_TIP = "9" * 40


async def test_a_stall_landing_records_the_consolidated_tip_as_this_lanes_next_act():
    """The landing is a commit act of this lane, so the record carries it.

    A run that stalls has its best iteration consolidated onto the deliverable
    branch, and the rows of the record ARE the acts a re-entry resolves
    (KOD-681). Without this row the resolution answers with the loop tip the
    run slipped back to, and the lane would resume from the work the landing
    was chosen over (KOD-705). Driven through the whole native fire, so the
    row is the one the landing step itself wrote.
    """
    port = tracker()
    publisher = FakeRefPublisher()
    # The branch this run mints is drawn in ``prepare``, and the repository is
    # the branch the run works on: it is told which one once the run has named
    # it, before any read of it.
    repo = LaneRepo(branch="unnamed")
    fire = engine(
        criteria=CountingCriteria(tracker=port),
        real_loop=True,
        executor=NativeExecutor([native_evaluation(failed=True)]),
        git=LaneGit(repo),
        source=LaneSource(repo),
        persister=LanePersister(repo),
        forge=lane_forge(),
        merger=FakeBranchMerger(merge_sha=LANDED_TIP),
        ref_publisher=publisher,
    )
    state, config = fire.prepare(
        prompt="Implement the current Checks.",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url=REPO_URL,
        base_spec=trunk_base("main"),
        scope=SCOPE_OF_SUBJECT,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key=JOB,
        surface_holder=JOB,
    )
    repo.branch = state["ralph_branch"]

    async for _ in fire.native_graph.astream(
        state, config=config, stream_mode="custom"
    ):
        pass

    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=SUBJECT, lane_key=SUBJECT
    )
    # The loop's own commit is still the row before it, and the landing act is
    # the last: two acts, the second at the tip the consolidation returned.
    landed = record.commits[-1]
    assert (landed.sha, landed.issue_id) == (LANDED_TIP, SUBJECT)
    assert landed.subject == LANDING_ROW_SUBJECT
    assert len(record.commits) == 2
    # The pre-landing tip is the sha the best iteration was published AT, and
    # it is not the answer: the landing consolidated it onto the deliverable
    # branch, and the tip of that branch is where the lane's best work stands.
    published = publisher.calls[0]["commit_sha"]
    assert published == record.commits[0].sha != LANDED_TIP
    # And what a re-entry resolves off this record, through the LOOP role, is
    # the landed tip — by sha, the reader's own function over the record the
    # landing left.
    assert recorded_commit(record=record).sha == LANDED_TIP


#: Where a consolidation that could not integrate reports the deliverable
#: branch standing: a sha of the merger's own, which nothing landed.
UNMOVED_TIP = "8" * 40


async def stalled_twice(*, merger=None, ref_publisher=None):
    """A native fire whose two iterations grade the same, run to its terminal.

    The earlier of two equal gradings is the best, so the best iteration is
    not the loop's last commit: an act recorded at it would be a row of its
    own after the loop's two, and so would be visible here.
    """
    port = tracker()
    repo = LaneRepo(branch="unnamed")
    fire = engine(
        criteria=CountingCriteria(tracker=port),
        real_loop=True,
        max_iterations=2,
        executor=NativeExecutor(
            [native_evaluation(failed=True), native_evaluation(failed=True)]
        ),
        git=LaneGit(repo),
        source=LaneSource(repo),
        persister=LanePersister(repo),
        forge=lane_forge(),
        merger=merger,
        ref_publisher=ref_publisher,
    )
    state, config = fire.prepare(
        prompt="Implement the current Checks.",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url=REPO_URL,
        base_spec=trunk_base("main"),
        scope=SCOPE_OF_SUBJECT,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key=JOB,
        surface_holder=JOB,
    )
    repo.branch = state["ralph_branch"]

    async for _ in fire.native_graph.astream(
        state, config=config, stream_mode="custom"
    ):
        pass

    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=SUBJECT, lane_key=SUBJECT
    )
    return repo, record


async def test_a_landing_not_integrated_records_the_best_iteration_as_the_next_act():
    """A best iteration the deliverable could not take is still the lane's best act.

    The landing publishes the best iteration under a ref of its own and the
    consolidation reports it divergent, so the deliverable branch did not
    move. The best commit is one of the loop branch's own commits, so it is
    recorded as the lane's next act: re-entry resolves it, and not the loop
    tip the run slipped back to (KOD-705, KOD-875). The sha the consolidation
    reports the deliverable standing at is no act of this lane.
    """
    publisher = FakeRefPublisher()
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT, feature_tip_sha=UNMOVED_TIP
            )
        ]
    )
    repo, record = await stalled_twice(merger=merger, ref_publisher=publisher)

    # The consolidation was reached, once, for the published best ref, and it
    # answered divergent: the premise of this arm, observed.
    assert len(publisher.calls) == 1
    assert len(merger.calls) == 1
    consolidated = merger.calls[0]
    assert consolidated["status"] is ConsolidationStatus.DIVERGENT
    assert (
        consolidated["source_branch"]
        == publisher.calls[0]["ref"]
        == best_iteration_ref(str(consolidated["feature_branch"]))
    )
    published = publisher.calls[0]["commit_sha"]
    assert len(repo.shas) == 2
    assert record.commits[-1].sha == published == repo.shas[0] != repo.shas[-1]
    assert record.commits[-1].subject == LANDING_ROW_SUBJECT
    assert UNMOVED_TIP not in {row.sha for row in record.commits}


async def test_a_forge_less_stall_records_the_best_iteration_as_the_next_act():
    """With the publisher withheld, as the forge-less composition does, nothing lands.

    Nothing is published and nothing consolidated, but the run still has a
    best iteration, and it is one of the loop branch's own commits: it is
    recorded as the lane's next act, so re-entry resolves it and not the loop
    tip the run slipped back to (KOD-705, KOD-875).
    """
    merger = FakeBranchMerger()
    repo, record = await stalled_twice(merger=merger)

    # No publisher exists to call, and the consolidation that would follow a
    # publish was never asked: the landing returned before either.
    assert merger.calls == []
    assert len(repo.shas) == 2
    assert record.commits[-1].sha == repo.shas[0] != repo.shas[-1]
    assert [row.sha for row in record.commits] == [*repo.shas, repo.shas[0]]


class CutAtItsBase(FakeWorkspaceProvider):
    """A tree cut fresh for a branch other than the repository's own stands at its base.

    The repository's own branch is the one its commits are made on, so its
    tree stands at the repository head. Any other loop branch cut fresh is
    a tree at the commit its base resolves to, as a real checkout of that
    base is: the head the amendment guard begins a round from, and the head
    a persist of a round that committed nothing finds.
    """

    def __init__(self, *, git: LaneGit, source: LaneSource) -> None:
        super().__init__(git=git)
        self.lane_git = git
        self.source = source

    async def acquire(self, **kwargs):
        path = await super().acquire(**kwargs)
        if (
            kwargs.get("create_branch", True)
            and kwargs.get("branch_name") != self.lane_git.repo.branch
        ):
            self.lane_git.heads[path] = await self.source.resolve_commit(
                cwd=path, ref=kwargs["ref"]
            )
        return path


class CommitsFirst(LanePersister):
    """Commits the lane's first *limit* iterations and none after them.

    What an iteration that changed nothing leaves is what ``ChangePersister``
    leaves: a clean tree whose head the remote does not hold is pushed as it
    stands, and the receipt names that head as the agent's own commit. A
    round that committed nothing stands at its cut point, so its fresh loop
    branch is pushed there, and a later iteration on a branch the remote
    already holds at its head pushes nothing. Every branch a persist was
    asked for is kept, committed to or not, and so is every branch this
    double pushed without a commit, at the sha it pushed.
    """

    def __init__(self, repo: LaneRepo, git: LaneGit, *, limit: int) -> None:
        super().__init__(repo)
        self.git = git
        self.limit = limit
        self.asked: list[str] = []
        self.pushed: dict[str, str] = {}

    async def persist(self, **kwargs):
        self.asked.append(kwargs["branch"])
        if len(self.repo.shas) < self.limit:
            return await super().persist(**kwargs)
        path, branch = kwargs["workspace_path"], kwargs["branch"]
        before_commit = kwargs.get("before_commit")
        before_publish = kwargs.get("before_publish")
        if before_commit is not None:
            await before_commit()
        head = await self.git.current_sha(path)
        remote_tip = self.pushed.get(branch)
        if remote_tip is None:
            remote_tip = await self.git.remote_branch_sha(
                path, self.repo.remote, branch
            )
        if remote_tip == head:
            return None
        if before_publish is not None:
            await before_publish(head)
        self.pushed[branch] = head
        return PersistResult(
            commit_sha=head,
            branch=branch,
            message="the commit this branch was cut at",
            source=PersistSource.AGENT_DIRECT_COMMIT,
        )


class PushedSource(LaneSource):
    """Resolves this lane's refs, and a branch pushed without a commit to its sha."""

    def __init__(self, repo: LaneRepo, persister: CommitsFirst) -> None:
        super().__init__(repo)
        self.persister = persister

    async def resolve_commit(self, *, cwd: str, ref: str) -> str:
        pushed = self.persister.pushed.get(ref)
        if pushed is not None:
            return pushed
        return await super().resolve_commit(cwd=cwd, ref=ref)


async def stalled_after_an_empty_round(*, merger, ref_publisher=None):
    """A native fire whose best is in round one and whose remediation round is empty.

    Round one commits twice and grades both the same, so its best is its
    first commit and not its tip. The remediation round draws a loop branch
    of its own, cut at the trunk, and commits nothing: the persister pushes
    that branch at its cut point, as ``ChangePersister`` does, and the round
    ends. The run then stalls into the landing *merger* and *ref_publisher*
    decide.
    """
    port = tracker()
    repo = LaneRepo(branch="unnamed")
    git = LaneGit(repo)
    persister = CommitsFirst(repo, git, limit=2)
    source = PushedSource(repo, persister)
    executor = NativeExecutor([native_evaluation(failed=True) for _ in range(4)])
    fire = engine(
        criteria=CountingCriteria(tracker=port),
        real_loop=True,
        max_iterations=2,
        remediation_rounds=1,
        executor=executor,
        git=git,
        source=source,
        workspace=CutAtItsBase(git=git, source=source),
        persister=persister,
        forge=lane_forge(),
        merger=merger,
        ref_publisher=ref_publisher,
    )
    state, config = fire.prepare(
        prompt="Implement the current Checks.",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url=REPO_URL,
        base_spec=trunk_base("main"),
        scope=SCOPE_OF_SUBJECT,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key=JOB,
        surface_holder=JOB,
    )
    first_round = state["ralph_branch"]
    # The repository double's own branch is round one's, the only one a
    # commit was made on.
    repo.branch = first_round

    async for _ in fire.native_graph.astream(
        state, config=config, stream_mode="custom"
    ):
        pass

    # The premise, observed: two commits pushed on round one's branch, one
    # remediation round, and that round asked to persist on a branch of its
    # own, committed nothing there, and had that branch pushed at the trunk.
    assert len(repo.shas) == 2
    assert [call["branch"] for call in persister.calls] == [first_round] * 2
    assert len(executor.remediation_prompts) == 1
    assert persister.asked[:2] == [first_round] * 2
    empty_rounds = set(persister.asked[2:])
    assert len(empty_rounds) == 1
    assert first_round not in empty_rounds
    assert persister.pushed == dict.fromkeys(empty_rounds, TRUNK_SHA)

    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=SUBJECT, lane_key=SUBJECT
    )
    return port, repo, first_round, record


async def test_a_stall_whose_last_round_committed_nothing_re_enters_at_its_best():
    """The best act is bound to the loop branch that holds it, not the last one.

    Round one commits twice and grades both the same, so its best is its
    first commit and not its tip; the remediation round draws a loop branch
    of its own and commits nothing. A round that committed nothing may push
    its branch at its cut point, but that push is not an iteration commit
    and never replaces an earlier best. Forge-less, so nothing is published
    or consolidated. The landing row names the best commit and not the
    trunk the empty round was cut at, and it is bound to round one's
    branch: a row bound to the empty round's would re-enter at the trunk.
    A re-entry resolves the best commit (KOD-705).
    """
    port, repo, first_round, landed = await stalled_after_an_empty_round(
        merger=FakeBranchMerger()
    )

    assert landed.commits[-1].sha == repo.shas[0] != repo.shas[-1]
    assert landed.commits[-1].subject == LANDING_ROW_SUBJECT
    # The empty round's push is no act of this lane: no row stands at the
    # trunk it was cut at.
    assert TRUNK_SHA not in {row.sha for row in landed.commits}
    assert [row.sha for row in landed.commits] == [*repo.shas, repo.shas[0]]
    # The branch the record names is the one holding the best commit.
    assert landed.branch == first_round
    assert (
        await LaneGit(repo).remote_branch_sha("/tmp/fire", "origin", landed.branch)
        == repo.shas[-1]
    )
    # And a re-entry over that record and that remote resolves the best
    # commit, by sha: the loop branch stands past it, so none is carried.
    entry = await LaneEntryReader(
        records=LaneRecordReader(tracker=port, operation=native_operation()),
        git=LaneGit(repo),
        remote="origin",
    ).read(
        issue_key=SUBJECT,
        open_criteria=OWED_KEYS,
        repo_path="/tmp/fire",
        implied_base=trunk_base("main"),
    )
    assert isinstance(entry, ResumedLane)
    assert (entry.loop_branch, entry.head_sha) == (None, repo.shas[0])


async def test_a_divergent_landing_after_an_empty_round_binds_the_best_branch():
    """On the divergent arm the landing row is bound to the branch holding the best.

    The best is published under a ref of its own and the consolidation
    reports it divergent, so the row is the best commit itself, one of round
    one's commits. The empty remediation round's branch holds the trunk it
    was cut at and no commit of this lane, so the record names round one's
    branch (KOD-705).
    """
    publisher = FakeRefPublisher()
    merger = FakeBranchMerger(
        consolidation_outcomes=[
            ConsolidationOutcome(
                status=ConsolidationStatus.DIVERGENT, feature_tip_sha=UNMOVED_TIP
            )
        ]
    )
    _, repo, first_round, record = await stalled_after_an_empty_round(
        merger=merger, ref_publisher=publisher
    )

    assert [call["status"] for call in merger.calls] == [ConsolidationStatus.DIVERGENT]
    assert publisher.calls[0]["commit_sha"] == repo.shas[0]
    assert record.commits[-1].sha == repo.shas[0] != repo.shas[-1]
    assert record.commits[-1].subject == LANDING_ROW_SUBJECT
    assert TRUNK_SHA not in {row.sha for row in record.commits}
    assert record.branch == first_round


async def test_an_integrated_landing_after_an_empty_round_binds_the_best_branch():
    """On the integrated arm the landing row is bound to the branch holding the best.

    The best is published and fast-forwarded onto the deliverable branch, so
    the row is the consolidated tip. The empty remediation round's branch
    holds the trunk it was cut at and no commit of this lane, so the record
    names round one's branch (KOD-705).
    """
    publisher = FakeRefPublisher()
    merger = FakeBranchMerger(merge_sha=LANDED_TIP)
    _, repo, first_round, record = await stalled_after_an_empty_round(
        merger=merger, ref_publisher=publisher
    )

    assert [call["status"] for call in merger.calls] == [
        ConsolidationStatus.FAST_FORWARDED
    ]
    assert publisher.calls[0]["commit_sha"] == repo.shas[0]
    assert record.commits[-1].sha == LANDED_TIP
    assert record.commits[-1].subject == LANDING_ROW_SUBJECT
    assert TRUNK_SHA not in {row.sha for row in record.commits}
    assert record.branch == first_round
