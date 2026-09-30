"""The scope run's stages: prep reads the criteria whole, the gate counts states."""

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import cast

import pytest

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains.scope_stages import ScopeStages
from kodezart.composition import engine as engine_module
from kodezart.composition.engine import build_workflow_engine
from kodezart.config.app import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import AgentRunner, PromptSetProvider, ScopeMemberPager
from kodezart.domain.criterion_creation import criterion_body
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import (
    CriterionId,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import (
    TrackerIssue,
    WorkflowStateKind,
)
from kodezart.types.domain.workflow import ExecutionContext, WorkflowState
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeAgentRunner,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_pass import example_config

PARENT = ScopeRef(kind=ScopeKind.ISSUE, key="FIRE-1")
# Prep reads nothing off the graph state; the run's address is the config.
_EMPTY = cast(WorkflowState, {})


def _issue(
    key: str,
    *,
    parent: str | None = PARENT.key,
    check: str | None = None,
    state: WorkflowStateKind = WorkflowStateKind.UNSTARTED,
) -> TrackerIssue:
    """A member below the parent; a criterion when it is given a Check."""
    return make_tracker_issue(
        key,
        parent_key=parent,
        state_kind=state,
        state_name=state.value,
        body=(
            "An issue."
            if check is None
            else criterion_body(parent_key=parent or "", check=check, do="Do it.")
        ),
        issue_labels=frozenset() if check is None else frozenset({"criterion"}),
    )


def _config() -> dict[str, object]:
    execution = ExecutionContext(
        prompt="Deliver the parent.",
        repo_path="/checkout",
        repo_url="https://github.com/example/project",
        cache_key="cache",
        base_spec=trunk_base("main"),
        permission_mode=PermissionMode.ACCEPT_EDITS,
        allowed_tools=["Read"],
        scope=PARENT,
    )
    return {"configurable": execution.model_dump()}


def _stages(runner: FakeAgentRunner, board: FakeTrackerPort) -> ScopeStages:
    return ScopeStages(
        runner=runner,
        prompts=load_registry(bindings=dict(bindings_for(example_config()))),
        skills=SUPPRESS_ALL_SKILLS,
        members=board,
        working_dir="/work",
    )


async def test_prep_reads_every_criterion_below_the_parent_across_pages() -> None:
    """Five criteria at two depths, one plain issue: three pages, one roster.

    The roster keeps the order the pages list the criteria in.

    The double pages two members at a time, so the roster only comes out
    whole if prep reads past the first page. The one session prep opens is
    the organize session: the criteria come from the tracker port, not from
    a second, question session.
    """
    board = FakeTrackerPort(
        issues=[
            _issue(PARENT.key, parent=None),
            _issue("FIRE-2"),
            _issue("FIRE-7", check="The seventh holds."),
            _issue("FIRE-3", check="The third holds."),
            _issue(
                "FIRE-4", check="The fourth holds.", state=WorkflowStateKind.COMPLETED
            ),
            _issue("FIRE-5", parent="FIRE-2", check="The fifth holds."),
            _issue("FIRE-6", parent="FIRE-2", check="The sixth holds."),
        ]
    )
    runner = FakeAgentRunner([])

    update = await _stages(runner, board).prep(_EMPTY, _config())

    assert update["criterion_set"] == TrackerCriterionSet(
        criteria=[
            TrackerCriterion(id=CriterionId(key), text=text)
            for key, text in (
                ("FIRE-7", "The seventh holds."),
                ("FIRE-3", "The third holds."),
                ("FIRE-4", "The fourth holds."),
                ("FIRE-5", "The fifth holds."),
                ("FIRE-6", "The sixth holds."),
            )
        ]
    )
    spec = update["fire_spec"]
    assert isinstance(spec, TrackerSpec)
    assert spec.criteria == ("FIRE-7", "FIRE-3", "FIRE-4", "FIRE-5", "FIRE-6")
    assert board.issue_reads == [
        "FIRE-2",
        "FIRE-7",
        "FIRE-3",
        "FIRE-4",
        "FIRE-5",
        "FIRE-6",
    ]
    assert [call["session_type"] for call in runner.calls] == [
        SessionType.ORGANIZE_PASS
    ]


class _Pages:
    """A board whose listing pages are given, repeats and all."""

    def __init__(self, *pages: tuple[TrackerIssue, ...]) -> None:
        self._pages = pages
        self.asked_whole: list[bool] = []

    async def scope_member_pages(
        self, *, ref: ScopeRef, whole_bodies: bool = False
    ) -> AsyncIterator[Sequence[TrackerIssue]]:
        self.asked_whole.append(whole_bodies)
        for page in self._pages:
            yield page


async def test_a_criterion_key_listed_twice_is_held_once_where_it_first_stood() -> None:
    """The pages list one criterion twice: one criterion, the last Check read.

    The roster is shaped through the one native set builder, which takes a
    mapping, so the key keeps its first position and the Check the pages
    gave it last. The set has no uniqueness check of its own; this is the
    outcome prep chooses.
    """
    board = _Pages(
        (_issue("c/one", check="first text"), _issue("c/two", check="second")),
        (_issue("i/plain"), _issue("c/one", check="restated text")),
    )
    stages = ScopeStages(
        runner=FakeAgentRunner([]),
        prompts=load_registry(bindings=dict(bindings_for(example_config()))),
        skills=SUPPRESS_ALL_SKILLS,
        members=board,
        working_dir="/work",
    )

    update = await stages.prep(_EMPTY, _config())

    assert update["criterion_set"] == TrackerCriterionSet(
        criteria=[
            TrackerCriterion(id=CriterionId("c/one"), text="restated text"),
            TrackerCriterion(id=CriterionId("c/two"), text="second"),
        ]
    )
    spec = update["fire_spec"]
    assert isinstance(spec, TrackerSpec)
    assert spec.criteria == ("c/one", "c/two")


async def test_prep_with_no_criterion_below_the_parent_has_nothing_to_grade() -> None:
    board = FakeTrackerPort(issues=[_issue(PARENT.key, parent=None), _issue("FIRE-2")])

    update = await _stages(FakeAgentRunner([]), board).prep(_EMPTY, _config())

    assert update == {"criteria_infeasible": True}


async def test_prep_reads_each_check_whole_and_the_gate_reads_states_only() -> None:
    """A listing may cut a Check short: prep asks for whole bodies, the gate not."""
    board = _Pages((_issue("c/one", check="first text"),))
    stages = ScopeStages(
        runner=FakeAgentRunner([]),
        prompts=load_registry(bindings=dict(bindings_for(example_config()))),
        skills=SUPPRESS_ALL_SKILLS,
        members=board,
        working_dir="/work",
    )

    await stages.prep(_EMPTY, _config())
    await stages.scope_done(_EMPTY, _config())

    assert board.asked_whole == [True, False]


PROJECT = ScopeRef(kind=ScopeKind.PROJECT, key="project-one")


def _project_config() -> dict[str, object]:
    configurable = _config()["configurable"]
    assert isinstance(configurable, dict)
    return {"configurable": {**configurable, "scope": PROJECT.model_dump()}}


async def test_prep_reads_the_criteria_below_a_projects_members() -> None:
    """A criterion is minted with no project: the container's members carry it.

    The board lists two members in the project; each has a criterion child
    that no project listing reaches. Prep's roster still holds both.
    """
    board = FakeTrackerPort(
        issues=[
            _issue("FIRE-1", parent=None),
            _issue("FIRE-2", parent=None),
            _issue("FIRE-3", parent="FIRE-1", check="The third holds."),
            _issue("FIRE-4", parent="FIRE-2", check="The fourth holds."),
            _issue("FIRE-5", parent="ELSEWHERE-1", check="Not below a member."),
        ],
        scope_containers=[
            ScopeContainer(
                ref=PROJECT,
                name="project-one",
                description="",
                url="https://tracker.invalid/project/project-one",
                parent=None,
            )
        ],
        scope_memberships={PROJECT: ["FIRE-1", "FIRE-2"]},
    )

    update = await _stages(FakeAgentRunner([]), board).prep(_EMPTY, _project_config())

    spec = update["fire_spec"]
    assert isinstance(spec, TrackerSpec)
    assert spec.criteria == ("FIRE-3", "FIRE-4")


def _board(*issues: TrackerIssue) -> FakeTrackerPort:
    return FakeTrackerPort(issues=[_issue(PARENT.key, parent=None), *issues])


async def test_the_gate_passes_when_nothing_below_the_parent_is_open() -> None:
    board = _board(
        _issue("FIRE-2", state=WorkflowStateKind.COMPLETED),
        _issue("FIRE-3", state=WorkflowStateKind.CANCELED),
        _issue(
            "FIRE-4", parent="FIRE-2", check="Held.", state=WorkflowStateKind.COMPLETED
        ),
    )
    runner = FakeAgentRunner([])

    update = await _stages(runner, board).scope_done(_EMPTY, _config())

    assert update == {"review_passed": True}
    assert runner.calls == []


async def test_the_gate_fails_with_the_count_and_the_keys_read_off_every_page() -> None:
    """The one open issue sits on the last page: every page is read (KOD-1288)."""
    board = _board(
        _issue("FIRE-2", state=WorkflowStateKind.COMPLETED),
        _issue("FIRE-3", state=WorkflowStateKind.COMPLETED),
        _issue("FIRE-4", state=WorkflowStateKind.COMPLETED),
        _issue("FIRE-5", state=WorkflowStateKind.COMPLETED),
        _issue("FIRE-6", state=WorkflowStateKind.STARTED),
    )

    update = await _stages(FakeAgentRunner([]), board).scope_done(_EMPTY, _config())

    assert update == {
        "review_passed": False,
        "review_feedback": "Open below the parent: 1 of 5, among them FIRE-6.",
    }
    assert board.issue_reads == ["FIRE-2", "FIRE-3", "FIRE-4", "FIRE-5", "FIRE-6"]


async def test_the_gate_names_at_most_ten_open_keys() -> None:
    board = _board(*(_issue(f"K-{n}") for n in range(12)))

    update = await _stages(FakeAgentRunner([]), board).scope_done(_EMPTY, _config())

    assert update == {
        "review_passed": False,
        "review_feedback": (
            "Open below the parent: 12 of 12, among them "
            + ", ".join(f"K-{n}" for n in range(10))
            + "."
        ),
    }


async def test_a_parent_with_nothing_below_it_is_not_finished() -> None:
    update = await _stages(FakeAgentRunner([]), _board()).scope_done(_EMPTY, _config())

    assert update == {
        "review_passed": False,
        "review_feedback": "Nothing was read below the parent.",
    }


async def test_the_engine_hands_its_scope_tracker_to_the_scope_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The composition root's own builder, not a hand-assembled analogue."""
    handed: list[ScopeMemberPager] = []

    class Recording(ScopeStages):
        def __init__(
            self,
            *,
            runner: AgentRunner,
            prompts: PromptSetProvider,
            skills: SkillsSelection,
            members: ScopeMemberPager,
            working_dir: str,
        ) -> None:
            handed.append(members)
            super().__init__(
                runner=runner,
                prompts=prompts,
                skills=skills,
                members=members,
                working_dir=working_dir,
            )

    monkeypatch.setattr(engine_module, "ScopeStages", Recording)
    tracker = FakeTrackerPort()
    workspace = FakeWorkspaceProvider()

    build_workflow_engine(
        repositories=(),
        # The shared prompt fixture resolves its set for the reviewed mode.
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            scheduled_pass_working_dir=str(tmp_path / "passes"),
        ),
        agent_service=AgentService(
            git_base_url="https://github.com",
            executor=FakeAgentExecutor(events=[]),
            workspace=workspace,
            persister=FakeChangePersister(),
        ),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=None,
        scope_tracker=tracker,
        scope_registry=InMemoryJobRegistry(),
    )

    assert handed == [tracker]
