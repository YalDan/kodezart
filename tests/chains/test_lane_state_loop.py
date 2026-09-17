"""What a lane's own commits leave on its issue, driven through the real loop."""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import BranchRole, trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_event import RunEventKind
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
    ):
        self.repo = LaneRepo()
        self.port = tracker() if port is None else port
        self.criteria = TrackerCriteria(tracker=self.port)
        self.executor = NativeExecutor(evaluations)
        self.persister = LanePersister(self.repo)
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
            work_base_ref="main",
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


async def test_a_lane_killed_mid_loop_is_located_from_the_tracker_alone():
    lane = Lane(
        evaluations=[native_evaluation(failed=True), native_evaluation()],
        max_iterations=3,
    )

    def die(evaluation: int) -> None:
        if evaluation == 2:
            raise ConnectionResetError("the lane was killed mid-loop")

    lane.executor.on_evaluation = die
    events: list[object] = []
    with pytest.raises(ConnectionResetError):
        await lane.run(events)

    # Everything the run held is dropped: only the board survives the kill.
    at_kill = (lane.repo.head, lane.repo.pushed, tuple(lane.repo.shas))
    streamed = [
        event.commit_sha
        for event in events
        if isinstance(event, ResultEvent) and event.commit_sha
    ]
    port = lane.port
    del lane

    comment, record = await LaneRecordReader(
        tracker=port, operation=native_operation()
    ).read(issue_key=SUBJECT, lane_key=SUBJECT)
    assert comment.issue_key == SUBJECT
    assert record.branch == BRANCH
    assert record.head_sha == at_kill[0]
    assert record.pushed_head_sha == at_kill[1]
    assert [row.sha for row in record.commits] == list(at_kill[2])
    assert len(record.commits) == 2
    assert record.pr is None
    assert [row.sha for row in record.commits] == streamed
