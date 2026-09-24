"""The commit act and its record are one operation, not two ordered steps."""

import inspect
from collections import Counter
from datetime import timedelta

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_state import LaneBinding
from kodezart.types.domain.session import PermissionMode, SessionType, ToolPreset
from kodezart.types.domain.tracker import (
    IssueRelation,
    IssueRelationKind,
    WorkflowStateKind,
)
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    SUBJECT,
    NativeExecutor,
    native_operation,
    tracker,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
)
from tests.lane_fixture import (
    LaneGit,
    LanePersister,
    LaneRepo,
    LaneSource,
    RecordingAfterPublish,
)

BRANCH = "ralph/native-test"
REMOTE = "origin"
REPO_URL = "https://forge.example/owner/repo"


def lane() -> LaneBinding:
    return LaneBinding(
        lane_key=SUBJECT,
        body_digest="a" * 64,
        loop_branch=BRANCH,
        deliverable_branch="feature/native-test",
        base=trunk_base("main"),
        repo_url=REPO_URL,
        repo_path=None,
        run_id="actual-parent-job",
        visibility=RepoVisibility.PUBLIC,
    )


class Arm:
    """The native commit path, wired as composition wires it, over one lane."""

    def __init__(self) -> None:
        self.repo = LaneRepo(branch=BRANCH, remote=REMOTE)
        self.git = LaneGit(self.repo)
        self.source = LaneSource(self.repo)
        self.port = tracker()
        self.executor = NativeExecutor([])
        self.workspace = FakeWorkspaceProvider(git=self.git)
        self.persister = LanePersister(self.repo)
        self.service = AgentService(
            executor=self.executor,
            workspace=self.workspace,
            persister=self.persister,
            git_base_url="https://forge.example",
        )
        self.criteria = TrackerCriteria(tracker=self.port)
        self.writer = TrackerLaneStateWriter(
            tracker=self.port,
            operation=native_operation(),
            git=self.git,
            git_remote=REMOTE,
            forge=None,
            gate=PassThroughGate(),
        )

    async def guard(self):
        spec, _ = await self.criteria.read_entry(issue_key=SUBJECT)
        return NativeAmendments(
            tracker=self.port,
            operation=native_operation(),
            criteria=self.criteria,
            git=self.git,
            source=self.source,
            workspace=self.workspace,
            runner=self.service,
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            repositories=(),
            git_base_url="https://forge.example",
            gate=PassThroughGate(),
            max_verify_rounds=2,
            lease_seconds=900,
        ).for_writer(
            spec=spec,
            criteria=await self.criteria.read_current(spec=spec),
            base_ref=lane().base.base_branch,
            repo_url=REPO_URL,
            holder=lane().run_id,
            visibility=RepoVisibility.PUBLIC,
            stage=PromptKey.IMPLEMENTATION,
        )

    async def commit(self, *, after_publish, events=None):
        return await self.iterate(
            await self.guard(), after_publish=after_publish, events=events
        )

    async def iterate(self, guard, *, after_publish, events=None):
        """One native execution iteration under *guard*, as the loop drives it."""
        seen = [] if events is None else events
        async for event in self.service.stream_workflow(
            prompt="Implement the current Checks.",
            repo_url=REPO_URL,
            base_branch=lane().base.base_branch,
            branch_name=lane().deliverable_branch,
            ralph_branch=BRANCH,
            create_branch=True,
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=ToolPreset.IMPLEMENTATION,
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.TICKET_FIRE,
            visibility=RepoVisibility.PUBLIC,
            native_guard=guard,
            after_publish=after_publish,
        ):
            seen.append(event)
        return seen

    async def record_commit(self, workspace_path: str, receipt: PersistResult) -> None:
        await self.writer.record_commit(
            lane=lane(), workspace_path=workspace_path, receipt=receipt
        )

    async def recorded(self):
        _, record = await LaneRecordReader(
            tracker=self.port, operation=native_operation()
        ).read(issue_key=SUBJECT, lane_key=SUBJECT)
        return record

    async def branch_head(self):
        return await self.git.remote_branch_sha("/workspace", REMOTE, BRANCH)


def commit_shas(events):
    return [
        event.commit_sha
        for event in events
        if isinstance(event, ResultEvent) and event.commit_sha
    ]


async def test_a_native_commit_path_without_the_record_write_refuses():
    arm = Arm()
    guard = await arm.guard()
    with pytest.raises(NativeWriteRefusalError, match="lane record write"):
        async for _ in arm.service.stream_workflow(
            prompt="Implement the current Checks.",
            repo_url=REPO_URL,
            base_branch=lane().base.base_branch,
            branch_name=lane().deliverable_branch,
            ralph_branch=BRANCH,
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=ToolPreset.IMPLEMENTATION,
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.TICKET_FIRE,
            visibility=RepoVisibility.PUBLIC,
            native_guard=guard,
        ):
            raise AssertionError("the refused commit path yielded an event")
    assert arm.workspace.calls == []
    assert arm.executor.calls == []
    assert arm.persister.calls == []
    assert arm.repo.shas == []


async def test_the_persist_phase_does_not_complete_when_the_record_write_raises():
    arm = Arm()

    async def refuse(workspace_path: str, receipt: PersistResult) -> None:
        raise LookupError("the lane record could not be written")

    events: list[object] = []
    with pytest.raises(LookupError):
        await arm.commit(after_publish=refuse, events=events)
    assert commit_shas(events) == []
    assert arm.repo.shas != []
    assert arm.port.comments == []


async def test_the_recorded_head_equals_the_branch_head_after_each_commit():
    arm = Arm()
    for _ in range(3):
        events = await arm.commit(after_publish=arm.record_commit)
        record = await arm.recorded()
        assert commit_shas(events) == [arm.repo.head]
        assert record.head_sha == await arm.branch_head()
        assert record.pushed_head_sha == record.head_sha
    assert len((await arm.recorded()).commits) == 3

    skipped = RecordingAfterPublish()
    await arm.commit(after_publish=skipped)
    assert len(skipped.calls) == 1
    assert (await arm.recorded()).head_sha != await arm.branch_head()


def tracker_calls(port, monkeypatch) -> Counter[str]:
    """Count, by name, every call *port* answers from here on."""
    calls: Counter[str] = Counter()
    for name, _ in inspect.getmembers_static(type(port), inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        answer = getattr(port, name)

        async def counted(*args, _name=name, _answer=answer, **kwargs):
            calls[_name] += 1
            return await _answer(*args, **kwargs)

        monkeypatch.setattr(port, name, counted)
    return calls


async def test_one_iteration_reads_the_lane_authority_at_its_start_commit_and_push(
    monkeypatch,
):
    """KOD-1249: one reading of the subtree per barrier, and none between them.

    Each reading is one family read, one criteria read per member and one
    comment listing per member, the criteria and the owed Checks both taken
    from that one map. It is taken when the writer starts, before the harness
    commits and before it pushes; restoring a phase and reconciling a writer
    that claimed nothing read nothing. This double's persister asks the commit
    hook once; the git persister also asks it on entry, one reading more.

    At f4c2fed6 the same iteration answered 333 calls: 32 subtree readings
    (224 criteria reads) and 77 comment listings.
    """
    arm = Arm()
    guard = await arm.guard()
    calls = tracker_calls(arm.port, monkeypatch)

    events = await arm.iterate(guard, after_publish=RecordingAfterPublish())

    assert commit_shas(events) == [arm.repo.head]
    members = len(arm.port.issues)
    assert calls == {
        "scope_issues": 3,
        "read_criteria": 3 * members,
        "list_comments": 3 * members,
    }


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (
            {
                "title": "retitled while the writer worked",
                "assignee_key": "someone-else",
                "relations": (
                    IssueRelation(kind=IssueRelationKind.RELATED, issue_key=SUBJECT),
                ),
            },
            False,
        ),
        ({"body": "**Check:** a Check rewritten while the writer worked"}, True),
        ({"state_kind": WorkflowStateKind.STARTED, "state_name": "In Progress"}, True),
        ({"issue_labels": frozenset({"criterion", "decision"})}, True),
        ({"parent_key": DIRECT_DONE}, True),
        ({"parent_key": None}, True),
    ],
    ids=["other facts", "Check", "state kind", "labels", "parent", "left the subtree"],
)
async def test_the_writer_is_refused_only_over_a_fact_its_write_depends_on(
    change, refused
):
    """KOD-1249: a criterion is compared on identity, Check, state, labels, parent.

    A criterion given a new title, assignee or relation while the writer works
    (a mention elsewhere on the board adds a relation) still publishes. The
    same criterion given a new Check, state or label, moved under another
    parent or out of the subtree, refuses before the harness commits.
    """
    arm = Arm()
    guard = await arm.guard()

    def edit(_opened):
        issue = arm.port.issues[DIRECT_OWED]
        arm.port.issues[DIRECT_OWED] = issue.model_copy(
            update={**change, "updated_at": issue.updated_at + timedelta(minutes=1)}
        )

    arm.executor.on_execution = edit
    if refused:
        with pytest.raises(NativeWriteRefusalError):
            await arm.iterate(guard, after_publish=RecordingAfterPublish())
        assert arm.persister.calls == []
        assert arm.repo.shas == []
    else:
        events = await arm.iterate(guard, after_publish=RecordingAfterPublish())
        assert commit_shas(events) == [arm.repo.head]
