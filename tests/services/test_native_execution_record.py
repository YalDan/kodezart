"""The commit act and its record are one operation, not two ordered steps."""

import inspect
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.core.protocols import AfterPublish, NativeWriteGuard
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import TrackerCriterionSet
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
    criterion_body,
    native_operation,
    tracker,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
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

    async def guard(
        self, *, roster: TrackerCriterionSet | None = None
    ) -> NativeWriteGuard:
        """The writer's guard, holding *roster*, or what the board owes now."""
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
            criteria=roster or await self.criteria.read_current(spec=spec),
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

    async def iterate(
        self,
        guard: NativeWriteGuard,
        *,
        after_publish: AfterPublish,
        events: list[object] | None = None,
    ) -> list[object]:
        """One native execution iteration under *guard*, as the loop drives it."""
        seen: list[object] = [] if events is None else events
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


def commit_shas(events: Iterable[object]) -> list[str]:
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


def tracker_calls(
    port: FakeTrackerPort, monkeypatch: pytest.MonkeyPatch
) -> Counter[str]:
    """Count, by name, every call *port* answers from here on."""
    calls: Counter[str] = Counter()
    for name, _ in inspect.getmembers_static(type(port), inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        answer = getattr(port, name)

        async def counted(
            *args: object,
            _name: str = name,
            _answer: Callable[..., Awaitable[object]] = answer,
            **kwargs: object,
        ) -> object:
            calls[_name] += 1
            return await _answer(*args, **kwargs)

        monkeypatch.setattr(port, name, counted)
    return calls


def one_reading_per(members: int, readings: int) -> dict[str, int]:
    """The calls *readings* readings of the lane's authority answer.

    One reading is one family read, one criteria read per member and one
    comment listing per member: the criteria and the owed Checks are both
    taken from that one membership map, and the rulings are listed once.
    """
    return {
        "scope_issues": readings,
        "read_criteria": readings * members,
        "list_comments": readings * members,
    }


async def test_one_iteration_reads_the_lane_authority_at_its_start_and_before_its_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KOD-1249: one reading when the writer starts, one before the push.

    Restoring a phase, reconciling a writer that claimed nothing and the
    persister's commit hook read nothing. This double's persister asks the
    commit hook once; the git persister asks it twice for a dirty tree, and
    ``tests/services/test_native_amendments.py`` pins the same two readings
    through it.

    At f4c2fed6 the same iteration answered 333 calls: 32 subtree readings
    (224 criteria reads) and 77 comment listings.
    """
    arm = Arm()
    guard = await arm.guard()
    calls = tracker_calls(arm.port, monkeypatch)

    events = await arm.iterate(guard, after_publish=RecordingAfterPublish())

    assert commit_shas(events) == [arm.repo.head]
    assert arm.repo.pushed == arm.repo.head
    assert calls == one_reading_per(len(arm.port.issues), readings=2)


#: A criterion that joins the subtree while the writer works. It is already
#: Done, so it is no Check the writer owes and the owed set cannot see it.
JOINED = "fire/done-joined"
#: The refusal a changed fact of one criterion reaches, and none other: the
#: owed Checks and the named roster are unchanged in every case that expects it.
FACTS_CHANGED = "native criterion facts changed during writing"


@pytest.mark.parametrize(
    ("key", "change", "refusal"),
    [
        (
            DIRECT_OWED,
            {
                "title": "retitled while the writer worked",
                "assignee_key": "someone-else",
                "relations": (
                    IssueRelation(kind=IssueRelationKind.RELATED, issue_key=SUBJECT),
                ),
            },
            None,
        ),
        # A Check the roster does not hold: the owed Checks are unchanged.
        (
            DIRECT_DONE,
            {"body": "**Check:** a Check rewritten while the writer worked"},
            FACTS_CHANGED,
        ),
        # A held criterion someone else finished stays owed, its Check unchanged.
        (
            DIRECT_OWED,
            {"state_kind": WorkflowStateKind.COMPLETED, "state_name": "Done"},
            FACTS_CHANGED,
        ),
        (
            DIRECT_OWED,
            {"issue_labels": frozenset({"criterion", "decision"})},
            FACTS_CHANGED,
        ),
        (DIRECT_OWED, {"parent_key": DIRECT_DONE}, FACTS_CHANGED),
        (
            JOINED,
            {
                "parent_key": SUBJECT,
                "issue_labels": frozenset({"criterion"}),
                "state_kind": WorkflowStateKind.COMPLETED,
                "state_name": "Done",
                "body": criterion_body(JOINED),
            },
            FACTS_CHANGED,
        ),
        (DIRECT_OWED, {"parent_key": None}, "named native criterion left the subtree"),
    ],
    ids=[
        "other facts",
        "Check",
        "state kind",
        "labels",
        "parent",
        "joined the subtree",
        "left the subtree",
    ],
)
async def test_the_writer_is_refused_only_over_a_fact_its_write_depends_on(
    key: str, change: dict[str, object], refusal: str | None
) -> None:
    """KOD-1249: a criterion is compared on identity, Check, state, labels, parent.

    A criterion given a new title, assignee or relation while the writer works
    (a mention elsewhere on the board adds a relation) still publishes. A new
    Check, state or label, a move under another parent, or a criterion that
    joins or leaves the subtree refuses before the push. Each refused case
    but the last changes nothing the owed Checks or the named roster see, so
    the comparison of the criteria's own facts is the only one that refuses
    it. The authority is not read before the commit, so the harness commit is
    made and stays local.
    """
    arm = Arm()
    guard = await arm.guard()

    def edit(_opened: object) -> None:
        issue = arm.port.issues.get(key)
        arm.port.issues[key] = (
            make_tracker_issue(key).model_copy(update=change)
            if issue is None
            else issue.model_copy(
                update={**change, "updated_at": issue.updated_at + timedelta(minutes=1)}
            )
        )

    arm.executor.on_execution = edit
    if refusal is None:
        events = await arm.iterate(guard, after_publish=RecordingAfterPublish())
        assert commit_shas(events) == [arm.repo.head]
        assert arm.repo.pushed == arm.repo.head
    else:
        with pytest.raises(NativeWriteRefusalError, match=refusal):
            await arm.iterate(guard, after_publish=RecordingAfterPublish())
        assert arm.repo.shas == [arm.repo.head]
        assert arm.repo.pushed is None


async def test_a_roster_the_board_no_longer_owes_is_refused_at_the_start() -> None:
    """KOD-1249: at the start the owed Checks are the comparison that can refuse.

    The criteria and the rulings the writer starts on are the reading taken
    there, so only the roster the loop hands over can differ from it: one
    holding a Check the board no longer states is refused before any session.
    """
    arm = Arm()
    _, roster = await arm.criteria.read_entry(issue_key=SUBJECT)
    stale = TrackerCriterionSet(
        criteria=[
            criterion.model_copy(update={"text": "a Check the board no longer states"})
            if criterion.id == DIRECT_OWED
            else criterion
            for criterion in roster.criteria
        ]
    )
    guard = await arm.guard(roster=stale)

    with pytest.raises(NativeWriteRefusalError, match="Current Checks changed"):
        await arm.iterate(guard, after_publish=RecordingAfterPublish())
    assert arm.executor.calls == []
    assert arm.persister.calls == []
    assert arm.repo.shas == []
