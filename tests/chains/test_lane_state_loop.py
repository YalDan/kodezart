"""What a lane's own commits leave on its issue, driven through the real loop."""

import ast
import json
from pathlib import Path

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.core.protocols import LaneStateWriter
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.criterion_cross_off import UNDEMONSTRATED_REASON
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.domain.lane_record import RUN_STATE_PURPOSE, render_lane_record
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import ResultEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import BranchRole, trunk_base
from kodezart.types.domain.criterion_lifecycle import CrossOffState
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import (
    DIRECT_OWED,
    OWED_KEYS,
    SUBJECT,
    NativeExecutor,
    engine,
    native_evaluation,
    native_operation,
    tracker,
)
from tests.domain.test_criterion_cross_off import callers_of
from tests.lane_fixture import (
    LaneGit,
    LanePersister,
    LaneRepo,
    LaneSource,
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
        repo_url=REPO_URL,
        writes_lane_state=True,
        source=LaneSource,
        has_changes=False,
    ):
        self.work_base_ref = work_base_ref
        self.repo_url = repo_url
        self.repo = LaneRepo(branch=BRANCH)
        self.git = LaneGit(self.repo)
        self.git.has_changes_result = has_changes
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
        )
        self.loop = self.fire.implementation._quality_gate

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


def graded(passed) -> dict:
    """One evaluator echo per owed criterion, passing exactly *passed*."""
    return {
        "criteriaResults": [
            {
                "criterionId": key,
                "criterion": "an evaluator echo",
                "passed": key in passed,
                "reasoning": "Observed the selected check.",
            }
            for key in OWED_KEYS
        ]
    }


def closure(port) -> SubtreeClosure:
    """The rollup a walker reads a subject's finished state from."""
    return SubtreeClosure(
        facts=dict(port.issues), ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
    )


def completed(port) -> set[str]:
    return {
        key
        for key in OWED_KEYS
        if port.issues[key].state_kind is WorkflowStateKind.COMPLETED
    }


async def test_cross_offs_appear_on_the_tracker_between_iterations():
    """The board carries iteration n's cross-offs while the loop still runs.

    Read off the fake tracker at the instant the iteration event arrives,
    never off what the loop returns: a consumer that sees the event can go to
    the board and find exactly the criteria that iteration passed already
    moved, with the rest still owed and the subject untouched.
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
    observed: dict[int, tuple[set[str], bool, tuple[str, str]]] = {}
    events: list[object] = []

    async for event in lane.loop.run(**await lane.arguments()):
        events.append(event)
        if isinstance(event, WorkflowIterationEvent):
            observed[event.iteration] = (
                completed(lane.port),
                closure(lane.port).is_closed(SUBJECT),
                (
                    lane.port.issues[SUBJECT].state_name,
                    lane.port.issues[SUBJECT].body,
                ),
            )

    assert observed[1] == (first, False, subject_before)
    assert observed[2] == (set(OWED_KEYS), True, subject_before)
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


class DriftedHead(LaneSource):
    """A workspace whose head is not the sha the verdict would be stamped with."""

    async def resolve_commit(self, *, cwd, ref):
        if ref == "HEAD":
            return "0" * 40
        return await super().resolve_commit(cwd=cwd, ref=ref)


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


@pytest.mark.parametrize(
    "workspace",
    [
        pytest.param({"has_changes": True}, id="uncommitted-change"),
        pytest.param({"source": DriftedHead}, id="head-elsewhere"),
    ],
)
async def test_a_workspace_that_is_not_the_graded_sha_yields_no_cross_off(workspace):
    """A verdict is only the branch's when the tree it read was the branch's.

    An uncommitted change in the grading workspace, or a head that is not the
    sha the verdict would be stamped with, means what the evaluator read was
    somebody's working copy. Nothing is written to any sub-issue, every result
    carries the fixed reason in place of a verdict, the iteration is rejected,
    and the writer is handed the fourth state for each criterion.
    """
    lane = Lane(evaluations=[native_evaluation()], **workspace)
    before = {
        key: (issue.state_name, issue.body) for key, issue in lane.port.issues.items()
    }
    states = recording(lane)

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
    """How much this board has been written: state moves and body edits."""
    return len(port.workflow_writes), len(port.issue_writes)


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
            id="never-accepted-over-three-iterations",
        ),
    ],
)
async def test_no_step_after_the_loop_writes_a_cross_off(fixture, crossed_off):
    """Whatever the loop ends as, the cross-offs are all it left behind.

    The whole fire is driven, so consolidation, the post-merge review and the
    terminal step all run after the loop's last iteration event. What the
    board has been written is counted at that event and again at the end, and
    the two are equal: the terminal aggregates and reports and is the first
    writer of nothing.
    """
    lane = Lane(**fixture)
    at_last_iteration: list[tuple[int, int]] = []

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

    assert at_last_iteration
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
    } == {LOOP: [f"{RalphLoop.__name__}._evaluate_node"]}
