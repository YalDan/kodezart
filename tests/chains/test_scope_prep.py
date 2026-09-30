"""The scope run's stages: prep reads the board's criteria, the gate counts."""

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
from kodezart.types.domain.agent import ResultEvent, ScopeItem, ScopeOpenCount
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import (
    CriterionId,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.scope import ScopeKind, ScopeRef
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


def _answer(structured: dict[str, object] | None) -> ResultEvent:
    return ResultEvent(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="scope",
        structured_output=structured,
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


def _done(
    open_count: int, *, total: int | None = None, keys: tuple[str, ...] = ()
) -> dict[str, object]:
    return ScopeOpenCount(
        open_count=open_count,
        total_count=total,
        sample=[ScopeItem(key=key, title=f"Title of {key}") for key in keys],
        reason="Read from one filtered query.",
    ).model_dump(by_alias=True)


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

    async def scope_member_pages(
        self, *, ref: ScopeRef
    ) -> AsyncIterator[Sequence[TrackerIssue]]:
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


async def test_the_gate_passes_when_the_count_of_open_members_is_zero() -> None:
    runner = FakeAgentRunner([_answer(_done(0, total=1575))])

    update = await _stages(runner, FakeTrackerPort()).scope_done(_EMPTY, _config())

    assert update == {"review_passed": True}
    [call] = runner.calls
    assert call["output_format"] == {
        "type": "json_schema",
        "schema": ScopeOpenCount.model_json_schema(),
    }


async def test_the_gate_fails_with_the_count_and_the_keys_it_was_given() -> None:
    runner = FakeAgentRunner([_answer(_done(3, total=40, keys=("K-0", "K-1", "K-2")))])

    update = await _stages(runner, FakeTrackerPort()).scope_done(_EMPTY, _config())

    assert update == {
        "review_passed": False,
        "review_feedback": (
            "Open below the parent: 3 of 40, among them K-0, K-1, K-2. "
            "Read from one filtered query."
        ),
    }


async def test_the_gate_names_at_most_ten_keys_and_no_total_it_was_not_given() -> None:
    keys = tuple(f"K-{n}" for n in range(12))
    runner = FakeAgentRunner([_answer(_done(250, keys=keys))])

    update = await _stages(runner, FakeTrackerPort()).scope_done(_EMPTY, _config())

    assert update["review_passed"] is False
    assert update["review_feedback"] == (
        "Open below the parent: 250, among them "
        + ", ".join(keys[:10])
        + ". Read from one filtered query."
    )


async def test_an_unanswered_gate_fails_and_says_so() -> None:
    """The 1,575-member case: the session ends with no structured answer."""
    runner = FakeAgentRunner([_answer(None)])

    update = await _stages(runner, FakeTrackerPort()).scope_done(_EMPTY, _config())

    assert update == {
        "review_passed": False,
        "review_feedback": "The scope-done question went unanswered.",
    }


def test_the_scope_done_answer_carries_a_count_and_no_member_list() -> None:
    assert set(ScopeOpenCount.model_json_schema()["properties"]) == {
        "openCount",
        "totalCount",
        "sample",
        "reason",
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
