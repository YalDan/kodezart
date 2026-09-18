"""What a lane's own commits leave on its issue, driven through the real loop."""

import json

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.lane_record import render_lane_record
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import BranchRole, trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.session import PermissionMode, ToolPreset
from tests.chains.test_native_fire import (
    SUBJECT,
    NativeExecutor,
    engine,
    native_evaluation,
    native_operation,
    tracker,
)
from tests.lane_fixture import (
    LaneGit,
    LanePersister,
    LaneRepo,
    LaneSource,
    lane_forge,
)

BRANCH = "ralph/fire-subject"
FEATURE = "feature/fire-subject"
JOB = "actual-parent-job"
REPO_URL = "https://github.com/owner/repo"


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
    ):
        self.work_base_ref = work_base_ref
        self.repo = LaneRepo()
        self.port = tracker() if port is None else port
        self.criteria = TrackerCriteria(tracker=self.port)
        self.executor = NativeExecutor(evaluations)
        self.persister = LanePersister(self.repo, publishes=publishes)
        self.fire = engine(
            criteria=self.criteria,
            executor=self.executor,
            real_loop=True,
            max_iterations=max_iterations,
            persister=self.persister,
            git=LaneGit(self.repo),
            source=LaneSource(self.repo),
            forge=forge,
            lane_operation=lane_operation,
        )
        self.loop = self.fire.implementation._quality_gate

    async def run(self, events=None):
        spec = await self.criteria.read_spec(issue_key=SUBJECT)
        current = await self.criteria.read_current(spec=spec)
        seen = [] if events is None else events
        async for event in self.loop.run(
            prompt="Implement the current Checks.",
            repo_path=None,
            repo_url=REPO_URL,
            feature_branch=FEATURE,
            ralph_branch=BRANCH,
            base_spec=trunk_base("main"),
            work_base_ref=self.work_base_ref,
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=ToolPreset.IMPLEMENTATION,
            acceptance_criteria=list(current.criteria),
            tracker_spec=spec,
            cache_key=JOB,
            surface_holder=JOB,
            repo_visibility=RepoVisibility.PUBLIC,
        ):
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


async def test_the_recorded_branch_url_is_the_forges_own_branch_page():
    lane = Lane(evaluations=[native_evaluation()], forge=lane_forge())
    await lane.run()
    assert (await lane.record()).branch_url == f"{REPO_URL}/tree/{BRANCH}"


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


async def test_an_operation_with_no_event_purpose_refuses_before_the_session():
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
                if purpose != RUN_EVENT_PURPOSE
            },
            issue_labels={"decision": "decision"},
        ),
    )

    with pytest.raises(OperationMemberAbsentError, match=RUN_EVENT_PURPOSE):
        await lane.run()

    assert lane.executor.execution_prompts == []
    assert lane.persister.calls == []
    assert lane.repo.shas == []
    assert port.comments == []


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
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=3,
        publishes=lambda commits: commits < 2,
    )
    port = lane.port
    at_die: list[list[tuple[str, str]]] = []

    def die(evaluation: int) -> None:
        if evaluation == 2:
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
    assert len(record.commits) == 2
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

    def die(evaluation: int) -> None:
        if evaluation == 2:
            raise ConnectionResetError("the lane was killed mid-loop")

    lane.executor.on_evaluation = die
    with pytest.raises(ConnectionResetError):
        await lane.run()
    del lane

    comment, record = await LaneRecordReader(
        tracker=port, operation=native_operation()
    ).read(issue_key=SUBJECT, lane_key=SUBJECT)
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

    async def kill_the_second_record_write(*, marker: str, **rest):
        if marker.startswith(f"[{prefix}:"):
            written.append(marker)
            if len(written) == 2:
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
