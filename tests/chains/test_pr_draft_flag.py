"""The draft flag tells the truth (KOD-1294).

Every pull request the engine opens is a draft, and exactly one write lifts
it: the readiness flip, made when the acceptance gate cleared, the final
review passed and the checks are green at the pushed head.  A stalled run's
pull request, a run whose checks failed, and a run whose checks are not
monitored all stay drafts.
"""

import uuid

from kodezart.chains.scope_stages import ScopeStages
from kodezart.domain.errors import ForgeAPIError
from kodezart.types.domain.agent import (
    ResultEvent,
    ScopeItem,
    ScopeItemsOutput,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from tests.chains.test_ralph_workflow import (
    _APP,
    _LIB,
    _CommitsBeyondTrunk,
    _make_engine,
    _stalled_gate,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeAgentRunner,
    FakeCIMonitor,
    FakePRCreator,
    FakeQualityGate,
    make_passing_evaluation_over,
)
from tests.prompts.sets import operation_registry

REPO = "https://github.com/owner/repo"


async def _run(engine):
    events = [
        e
        async for e in engine.run(
            scope=None,
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=REPO,
            base_spec=trunk_base("main"),
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]
    return next(e for e in events if isinstance(e, WorkflowCompleteEvent))


def _methods(creator: FakePRCreator) -> list[str]:
    return [str(call["method"]) for call in creator.calls]


async def test_an_accepted_run_with_green_checks_marks_its_pull_request_ready():
    creator = FakePRCreator()
    engine = _make_engine(pr_creator=creator, ci_monitor=FakeCIMonitor(passed=True))

    complete = await _run(engine)

    assert complete.outcome is WorkflowOutcome.ci_passed
    assert _methods(creator) == ["create_pr", "mark_ready_for_review"]
    assert creator.calls[0]["draft"] is True
    # The flip addresses the request the run opened: same repository, same
    # number, and the repository as the run resolved it (its ``.git`` form).
    assert creator.calls[1] == {
        "method": "mark_ready_for_review",
        "repo_url": creator.calls[0]["repo_url"],
        "pr_number": 1,
    }
    assert creator.drafts == {1: False}


async def test_a_stalled_run_opens_a_draft_and_never_marks_it_ready():
    """The gate did not clear, so the request asks a person to read a stall."""
    creator = FakePRCreator()
    engine = _make_engine(
        quality_gate=_stalled_gate(),
        pr_creator=creator,
        ci_monitor=FakeCIMonitor(passed=True),
    )

    complete = await _run(engine)

    assert complete.accepted is False
    assert complete.ci_status is CIStatus.passed
    assert _methods(creator) == ["create_pr"]
    assert creator.drafts == {1: True}


async def test_red_checks_leave_the_pull_request_a_draft():
    creator = FakePRCreator()
    engine = _make_engine(
        pr_creator=creator,
        ci_monitor=FakeCIMonitor(passed=False, summary="CI failed: ci/test"),
    )

    complete = await _run(engine)

    assert complete.ci_status is CIStatus.failed
    assert "mark_ready_for_review" not in _methods(creator)
    assert creator.drafts == {1: True}


async def test_unmonitored_checks_leave_the_pull_request_a_draft():
    """No green head can be shown, so nothing says the unit is finished."""
    creator = FakePRCreator()
    engine = _make_engine(pr_creator=creator, ci_monitor=None)

    complete = await _run(engine)

    assert complete.ci_status is CIStatus.not_monitored
    assert _methods(creator) == ["create_pr"]
    assert creator.drafts == {1: True}


async def test_a_forge_refusal_of_the_flip_is_logged_and_the_run_still_completes():
    creator = FakePRCreator(
        fail_mark_ready=ForgeAPIError(
            "refused", status_code=403, detail="POST /graphql"
        )
    )
    engine = _make_engine(pr_creator=creator, ci_monitor=FakeCIMonitor(passed=True))

    complete = await _run(engine)

    assert complete.outcome is WorkflowOutcome.ci_passed
    assert _methods(creator) == ["create_pr", "mark_ready_for_review"]
    assert creator.drafts == {1: True}


# -- Scope runs over several repositories ------------------------------------
#
# A scope run opens one draft per repository its branch gained commits in,
# and the readiness flip addresses every one of them by the repository it
# was opened in, never by the run's own repository.


class _ReviewOverScope(FakeAgentExecutor):
    """The post-merge review answers over the scope's own criterion key."""

    async def stream(self, **kwargs):
        if self._is_acceptance_criteria_schema(kwargs.get("output_format")):
            yield ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output={
                    "criteriaResults": [
                        {
                            "criterionId": "SCOPE-2",
                            "criterion": "It works",
                            "passed": True,
                            "reasoning": "Passing review.",
                        }
                    ]
                },
            )
            return
        async for event in super().stream(**kwargs):
            yield event


def _finished_board() -> FakeAgentRunner:
    """Every issue below the parent is done, so the review runs."""
    return FakeAgentRunner(
        events=[
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                structured_output=ScopeItemsOutput(
                    items=[
                        ScopeItem(
                            key="SCOPE-2", criterion=True, text="It works", done=True
                        )
                    ],
                    reason="Every issue below the parent is done.",
                ).model_dump(by_alias=True),
            )
        ]
    )


async def _run_finished_scope(
    tmp_path, creator: FakePRCreator, *, commits_beyond: dict[str, int]
) -> WorkflowCompleteEvent:
    engine = _make_engine(
        quality_gate=FakeQualityGate(
            events=[], evaluation=make_passing_evaluation_over("SCOPE-2")
        ),
        executor=_ReviewOverScope(events=[]),
        pr_creator=creator,
        ci_monitor=FakeCIMonitor(passed=True),
        git=_CommitsBeyondTrunk(commits_beyond),
        repositories=(_APP, _LIB),
        stages=ScopeStages(
            runner=_finished_board(),
            prompts=operation_registry(),
            skills=SUPPRESS_ALL_SKILLS,
            working_dir=str(tmp_path),
        ),
    )
    events = [
        e
        async for e in engine.run(
            scope=ScopeRef(kind=ScopeKind.ISSUE, key="SCOPE-1"),
            prompt="scope issue SCOPE-1",
            repo_path=None,
            repo_url=_APP.url,
            base_spec=trunk_base("main"),
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]
    return next(e for e in events if isinstance(e, WorkflowCompleteEvent))


def _repositories_of(creator: FakePRCreator, method: str) -> list[str]:
    return [str(call["repo_url"]) for call in creator.calls if call["method"] == method]


async def test_a_finished_scope_run_marks_every_repository_s_pull_request_ready(
    tmp_path,
):
    """Two repositories gained commits: two drafts opened, two flipped."""
    creator = FakePRCreator()

    complete = await _run_finished_scope(
        tmp_path, creator, commits_beyond={"main": 2, "trunk": 1}
    )

    assert complete.outcome is WorkflowOutcome.ci_passed
    assert _repositories_of(creator, "create_pr") == [_APP.url, _LIB.url]
    assert _repositories_of(creator, "mark_ready_for_review") == [_APP.url, _LIB.url]
    assert creator.drafts == {1: False, 2: False}


async def test_the_flip_addresses_the_repository_the_request_was_opened_in(
    tmp_path,
):
    """Work only in the other repository: the flip goes there, not to the run's."""
    creator = FakePRCreator()

    complete = await _run_finished_scope(tmp_path, creator, commits_beyond={"trunk": 1})

    assert complete.outcome is WorkflowOutcome.ci_passed
    assert _repositories_of(creator, "create_pr") == [_LIB.url]
    assert _repositories_of(creator, "mark_ready_for_review") == [_LIB.url]
    assert creator.drafts == {1: False}


async def test_a_refused_flip_in_one_repository_still_flips_the_other(tmp_path):
    """One forge refusal is logged; the remaining requests are still flipped."""
    creator = _RefuseFirstFlip()

    complete = await _run_finished_scope(
        tmp_path, creator, commits_beyond={"main": 2, "trunk": 1}
    )

    assert complete.outcome is WorkflowOutcome.ci_passed
    assert _repositories_of(creator, "mark_ready_for_review") == [_APP.url, _LIB.url]
    assert creator.drafts == {1: True, 2: False}


class _RefuseFirstFlip(FakePRCreator):
    """The forge refuses the first readiness flip and accepts the rest."""

    def __init__(self) -> None:
        super().__init__()
        self._refused = False

    async def mark_ready_for_review(self, *, repo_url: str, pr_number: int) -> None:
        if not self._refused:
            self._refused = True
            self.calls.append(
                {
                    "method": "mark_ready_for_review",
                    "repo_url": repo_url,
                    "pr_number": pr_number,
                }
            )
            raise ForgeAPIError("refused", status_code=403, detail="POST /graphql")
        await super().mark_ready_for_review(repo_url=repo_url, pr_number=pr_number)
