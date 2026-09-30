"""The draft flag tells the truth (KOD-1294).

Every pull request the engine opens is a draft, and exactly one write lifts
it: the readiness flip, made when the acceptance gate cleared, the final
review passed and the checks are green at the pushed head.  A stalled run's
pull request, a run whose checks failed, and a run whose checks are not
monitored all stay drafts.
"""

import uuid

from kodezart.domain.errors import ForgeAPIError
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.session import PermissionMode
from tests.chains.test_ralph_workflow import _make_engine, _stalled_gate
from tests.fakes import FakeCIMonitor, FakePRCreator

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
